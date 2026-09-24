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
[livery.toolroom.store.surface_at][] for one version's command line,
[livery.toolroom.store.Catalogue][] for what a consumer resolves against
and [livery.toolroom.store.resolve_lock][] for the repository's lock,
[livery.toolroom.store.Home][] for the store's directories, and
[livery.toolroom.store.Store][] to install, link, emit and fetch.
"""

from __future__ import annotations

from livery.toolroom.store._catalogue import (
    BUILD_FILE,
    POINTER,
    Catalogue,
    CatalogueError,
    Listed,
    build_current,
    read_pointer,
)
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
from livery.toolroom.store._fingerprint import tree_fingerprint
from livery.toolroom.store._home import TOOLS, URLS, Home
from livery.toolroom.store._lock import (
    LOCK_FILE,
    Lock,
    Locked,
    LockError,
    Requirement,
    resolve_lock,
)
from livery.toolroom.store._record import (
    ARCHES,
    DELTAS_DIR,
    DOWNLOAD_KINDS,
    HOSTS,
    KINDS,
    LAYOUT_KEYS,
    MODES,
    OPTION_KEYS,
    PACKAGE_VAR,
    PLATFORMS,
    SURFACE_PLATFORMS,
    TOOL_FILE,
    VERB_KEYS,
    Artifact,
    Deployment,
    Layout,
    Observation,
    Record,
    RecordError,
    Surface,
    class_name,
    default_mode,
    export_schema,
    host_key,
    observations,
    resolve,
    schema,
    surface_at,
    validate,
    version_key,
)
from livery.toolroom.store._record import Delta as RecordDelta
from livery.toolroom.store._spec import Option, ToolSpec, Verb
from livery.toolroom.store._stub import (
    NameCollision,
    render,
    render_observation,
    spec_from,
)

__all__ = [
    "ARCHES",
    "BUILD_FILE",
    "DELTAS_DIR",
    "DOWNLOAD_KINDS",
    "HOSTS",
    "KINDS",
    "LAYOUT_KEYS",
    "LINKS",
    "LOCK_FILE",
    "MODES",
    "OPTION_KEYS",
    "PACKAGE_VAR",
    "PLATFORMS",
    "POINTER",
    "SURFACE_PLATFORMS",
    "TOOLS",
    "TOOL_FILE",
    "URLS",
    "VERB_KEYS",
    "Artifact",
    "Catalogue",
    "CatalogueError",
    "Delta",
    "Deployment",
    "Ensured",
    "Event",
    "Fetched",
    "Home",
    "Layout",
    "Listed",
    "Lock",
    "LockError",
    "Locked",
    "NameCollision",
    "Observation",
    "Option",
    "Progress",
    "Record",
    "RecordDelta",
    "RecordError",
    "Requirement",
    "Store",
    "StoreError",
    "Surface",
    "ToolSpec",
    "Verb",
    "__version__",
    "build_current",
    "class_name",
    "default_mode",
    "export_schema",
    "host_key",
    "observations",
    "read_pointer",
    "render",
    "render_observation",
    "resolve",
    "resolve_lock",
    "schema",
    "silent",
    "spec_from",
    "surface_at",
    "tree_fingerprint",
    "validate",
    "version_key",
]

__version__ = "0.0.0"
