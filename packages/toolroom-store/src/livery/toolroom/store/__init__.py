"""The pinned tool store over strongroom.

A record names a tool, every version tracked, and per version and host
the artifact's URL and sha256 with the deployment resolved through the
record's layers; the store lands the artifact by that digest, extracts
it, collects it as a tree, and names the tree under
`tools/<name>@<version>` in a machine-wide strongroom store whose home
this package lays out; a checkout's bin directory links the
executables, and a delta says what to put on PATH.

Reach for [livery.toolroom.store.Record][] to read a record,
[livery.toolroom.store.resolve][] for one host's deployment,
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
from livery.toolroom.store._record import (
    ARCHES,
    DELTAS_DIR,
    DOWNLOAD_KINDS,
    HOSTS,
    KINDS,
    LAYOUT_KEYS,
    PACKAGE_VAR,
    PLATFORMS,
    TOOL_FILE,
    Artifact,
    Deployment,
    Layout,
    Record,
    RecordError,
    export_schema,
    host_key,
    resolve,
    schema,
    validate,
)
from livery.toolroom.store._record import Delta as RecordDelta

__all__ = [
    "ARCHES",
    "DELTAS_DIR",
    "DOWNLOAD_KINDS",
    "HOSTS",
    "KINDS",
    "LAYOUT_KEYS",
    "LINKS",
    "PACKAGE_VAR",
    "PLATFORMS",
    "TOOLS",
    "TOOL_FILE",
    "URLS",
    "Artifact",
    "Delta",
    "Deployment",
    "Ensured",
    "Event",
    "Fetched",
    "Home",
    "Layout",
    "Progress",
    "Record",
    "RecordDelta",
    "RecordError",
    "Store",
    "StoreError",
    "__version__",
    "export_schema",
    "host_key",
    "resolve",
    "schema",
    "silent",
    "validate",
]

__version__ = "0.0.0"
