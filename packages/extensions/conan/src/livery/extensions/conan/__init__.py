"""The conan extension's names another extension reads to build against its packages.

The conan extension builds a member whose recipe, ``conanfile.py``,
names a reference into the conan cache, and releases it. An extension
whose builds link such a member reads five of its names. The
workspace's conan members resolve one another from their sources
through the conan workspace file at the root,
[livery.extensions.conan.WORKSPACE_FILE][], whose text
[livery.extensions.conan.workspace_file][] renders.
[livery.extensions.conan.workspace_aside][] hides that file for a
block, so a build in it resolves each member from the conan cache.
A release's build saves the package it created out of the cache with
[livery.extensions.conan.save_cache][], one file per host, and
[livery.extensions.conan.restore_from_releases][] restores a released
version from the member's release on the forge.
"""

from __future__ import annotations

# The literal-False spelling the checkers honour without importing
# `typing`: the names below are what a checker sees, and at runtime
# `__getattr__` serves each from its module when it is read, so importing
# this module loads none of the extension's modules.
TYPE_CHECKING = False
if TYPE_CHECKING:
    from livery.extensions.conan._package import WORKSPACE_FILE as WORKSPACE_FILE
    from livery.extensions.conan._package import (
        restore_from_releases as restore_from_releases,
    )
    from livery.extensions.conan._package import save_cache as save_cache
    from livery.extensions.conan._package import workspace_aside as workspace_aside
    from livery.extensions.conan._package import workspace_file as workspace_file

__all__ = [
    "WORKSPACE_FILE",
    "restore_from_releases",
    "save_cache",
    "workspace_aside",
    "workspace_file",
]

# The module each lazily served name lives in.
_EXPORTS: dict[str, str] = {
    "WORKSPACE_FILE": "livery.extensions.conan._package",
    "restore_from_releases": "livery.extensions.conan._package",
    "save_cache": "livery.extensions.conan._package",
    "workspace_aside": "livery.extensions.conan._package",
    "workspace_file": "livery.extensions.conan._package",
}


def __getattr__(name: str) -> object:
    """Serve a public name from its module, each time it is read.

    The value is never kept here, so a test that patches the name in
    its module is read through.
    """
    import importlib

    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(
            f"module 'livery.extensions.conan' has no attribute {name!r}"
        )
    return getattr(importlib.import_module(module), name)
