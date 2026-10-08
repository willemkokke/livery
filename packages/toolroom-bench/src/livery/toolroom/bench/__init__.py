"""The bench: where toolroom's handles are made and refreshed.

The drivers that enumerate a tool's releases, the observation of a
release's help on the platform, the tool history, the stub generator
that writes the checked-in stubs of `livery.toolroom.tools`, the pin
verb, the docs generator for the per-tool pages, and the `fm tools.*`
verbs that run them. A consumer of the handles never installs this; a
workspace that keeps the handles current names it as an extension, and
footman's plugin loading is the only way in.

The package exposes its one group, `tasks`, the `tools` verbs, which
its plugin entry point names; and the refresh's data,
[livery.toolroom.bench.Refreshed][] and
[livery.toolroom.bench.submit_refresh][], for a scheduled job that
reads the release decision.
"""

from __future__ import annotations

# The literal-False spelling the checkers honour without importing
# `typing`: the names below are what a checker sees, and at runtime
# `__getattr__` serves each from its module on first use.
TYPE_CHECKING = False
if TYPE_CHECKING:
    from livery.toolroom.bench._tasks import Refreshed as Refreshed
    from livery.toolroom.bench._tasks import submit_refresh as submit_refresh
    from livery.toolroom.bench._tasks import tasks as tasks

__all__ = ["Refreshed", "__version__", "submit_refresh", "tasks"]

__version__ = "0.3.0"

# The module each lazily served name lives in. The group comes from
# _docsgen, which adds `toolroom.docs` to the group it imports, so the
# group served is the whole one.
_EXPORTS: dict[str, str] = {
    "Refreshed": "livery.toolroom.bench._tasks",
    "submit_refresh": "livery.toolroom.bench._tasks",
    "tasks": "livery.toolroom.bench._docsgen",
}


def __getattr__(name: str) -> object:
    """Serve a public name from its module on first use, then keep it."""
    import importlib

    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(
            f"module 'livery.toolroom.bench' has no attribute {name!r}"
        )
    value = getattr(importlib.import_module(module), name)
    globals()[name] = value
    return value
