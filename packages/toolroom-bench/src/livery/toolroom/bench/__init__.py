"""The bench: where toolroom's handles are made and refreshed.

The drivers that enumerate a tool's releases, the observation of a
release's help on the platform, the tool history, the stub generator
that writes the checked-in stubs of `livery.toolroom.tools`, the pin
verb, the docs generator for the per-tool pages, and the `fm tools.*`
verbs that run them. A consumer of the handles never installs this; a
workspace that keeps the handles current names it as a layer, and
footman's plugin loading is the only way in.

The package exposes its one group, `tasks`, the `tools` verbs, which
is what the plugin loader adopts; and the refresh's data,
[livery.toolroom.bench.Refreshed][] and
[livery.toolroom.bench.submit_refresh][], for a scheduled job that
reads the release decision.
"""

from __future__ import annotations

from livery.toolroom.bench import _docsgen as _docsgen
from livery.toolroom.bench._tasks import Refreshed, submit_refresh, tasks

__all__ = ["Refreshed", "__version__", "submit_refresh", "tasks"]

__version__ = "0.0.0"
