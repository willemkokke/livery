"""The pinned tool store over strongroom.

A spec names a tool, the version a checkout pins, and per host the
artifact's URL and sha256; the store lands the artifact by that pin,
extracts it, collects it as a tree, and names the tree under
`tools/<name>@<version>` in a machine-wide strongroom store whose
home this package lays out; a checkout's bin directory links the
executables, and a delta says what to put on PATH.

Reach for [livery.toolroom.store.Spec][] to read a spec,
[livery.toolroom.store.Home][] for the store's directories, and
[livery.toolroom.store.Store][] to install, link, emit and fetch.
"""

from __future__ import annotations

from livery.toolroom.store._engine import (
    LINKS,
    Delta,
    Ensured,
    Event,
    Fetched,
    Progress,
    Store,
    StoreError,
    silent,
)
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
    "LINKS",
    "PACKAGE_VAR",
    "PLATFORMS",
    "TOOLS",
    "URLS",
    "Definition",
    "Delta",
    "Ensured",
    "Event",
    "Fetched",
    "Home",
    "Progress",
    "Spec",
    "SpecError",
    "Store",
    "StoreError",
    "Version",
    "__version__",
    "export_schema",
    "host_key",
    "schema",
    "silent",
]

__version__ = "0.0.0"
