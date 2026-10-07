"""The documentation site, and what a generator in any package writes for it.

A generator writes a package's pages into the package's generated tree,
``docs/`` then [livery.extensions.docs.GENERATED][], and emits the nav
block that lists them beside them with
[livery.extensions.docs.write_nav_block][]; the package's authored
``docs/nav.toml`` places the block where the marker pair
[livery.extensions.docs.nav_block_markers][] gives sits. The site's
verbs, checks and CI jobs arrive through the extension's entry point and
its ``extension.toml``, so importing this module registers nothing.
"""

from __future__ import annotations

# The literal-False spelling the checkers honour without importing
# `typing`: the names below are what a checker sees, and at runtime
# `__getattr__` serves each from its module on first use, so importing
# the extension's names never loads the site.
TYPE_CHECKING = False
if TYPE_CHECKING:
    from livery.extensions.docs._navblocks import (
        nav_block_markers as nav_block_markers,
    )
    from livery.extensions.docs._navblocks import write_nav_block as write_nav_block
    from livery.workshop._docs_contract import GENERATED as GENERATED

__all__ = ["GENERATED", "nav_block_markers", "write_nav_block"]

# The module each lazily served name lives in.
_EXPORTS: dict[str, str] = {
    "GENERATED": "livery.workshop._docs_contract",
    "nav_block_markers": "livery.extensions.docs._navblocks",
    "write_nav_block": "livery.extensions.docs._navblocks",
}


def __getattr__(name: str) -> object:
    """Serve a public name from its module on first use, then keep it."""
    import importlib

    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(
            f"module 'livery.extensions.docs' has no attribute {name!r}"
        )
    value = getattr(importlib.import_module(module), name)
    globals()[name] = value
    return value
