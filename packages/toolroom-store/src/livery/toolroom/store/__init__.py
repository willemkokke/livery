"""The pinned tool store over strongroom.

A spec names a tool, the version a checkout pins, and per host the
artifact's URL and sha256; the store lands the artifact by that pin,
extracts it, collects it as a tree, and names the tree under
`tools/<name>@<version>` in a machine-wide strongroom store whose
home this package lays out. This phase carries the spec model and the
home; installing, emitting and fetching follow.

Reach for [livery.toolroom.store.Spec][] to read a spec and
[livery.toolroom.store.Home][] for the store's directories.
"""

from __future__ import annotations

from livery.toolroom.store._home import TOOLS, URLS, Home
from livery.toolroom.store._spec import (
    ARCHES,
    DOWNLOAD_KINDS,
    HOSTS,
    KINDS,
    PACKAGE_VAR,
    PLATFORMS,
    Definition,
    Spec,
    SpecError,
    Version,
    export_schema,
    host_key,
    schema,
)

__all__ = [
    "ARCHES",
    "DOWNLOAD_KINDS",
    "HOSTS",
    "KINDS",
    "PACKAGE_VAR",
    "PLATFORMS",
    "TOOLS",
    "URLS",
    "Definition",
    "Home",
    "Spec",
    "SpecError",
    "Version",
    "__version__",
    "export_schema",
    "host_key",
    "schema",
]

__version__ = "0.0.0"
