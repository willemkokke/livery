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

# The literal-False spelling the checkers honour without importing
# `typing`: the names below are what a checker sees, and at runtime
# `__getattr__` serves each from its module on first use, so a program
# that needs one name never pays for the modules it does not touch.
TYPE_CHECKING = False
if TYPE_CHECKING:
    from livery.toolroom.store._catalogue import BUILD_FILE as BUILD_FILE
    from livery.toolroom.store._catalogue import POINTER as POINTER
    from livery.toolroom.store._catalogue import Catalogue as Catalogue
    from livery.toolroom.store._catalogue import CatalogueError as CatalogueError
    from livery.toolroom.store._catalogue import Listed as Listed
    from livery.toolroom.store._catalogue import build_current as build_current
    from livery.toolroom.store._catalogue import read_pointer as read_pointer
    from livery.toolroom.store._engine import LINKS as LINKS
    from livery.toolroom.store._engine import Delta as Delta
    from livery.toolroom.store._engine import Ensured as Ensured
    from livery.toolroom.store._engine import Event as Event
    from livery.toolroom.store._engine import Fetched as Fetched
    from livery.toolroom.store._engine import Progress as Progress
    from livery.toolroom.store._engine import Store as Store
    from livery.toolroom.store._engine import StoreError as StoreError
    from livery.toolroom.store._engine import bun_global_project as bun_global_project
    from livery.toolroom.store._engine import npm_cli as npm_cli
    from livery.toolroom.store._engine import silent as silent
    from livery.toolroom.store._fetch import FetchError as FetchError
    from livery.toolroom.store._fetch import UnpackError as UnpackError
    from livery.toolroom.store._fetch import api_headers as api_headers
    from livery.toolroom.store._fetch import fetch_bytes as fetch_bytes
    from livery.toolroom.store._fetch import fetch_file as fetch_file
    from livery.toolroom.store._fetch import fetch_json as fetch_json
    from livery.toolroom.store._fetch import unpack as unpack
    from livery.toolroom.store._fingerprint import tree_fingerprint as tree_fingerprint
    from livery.toolroom.store._home import TOOLS as TOOLS
    from livery.toolroom.store._home import URLS as URLS
    from livery.toolroom.store._home import Home as Home
    from livery.toolroom.store._lock import GRAPHS as GRAPHS
    from livery.toolroom.store._lock import LOCK_FILE as LOCK_FILE
    from livery.toolroom.store._lock import Graph as Graph
    from livery.toolroom.store._lock import Lock as Lock
    from livery.toolroom.store._lock import Locked as Locked
    from livery.toolroom.store._lock import LockError as LockError
    from livery.toolroom.store._lock import Requirement as Requirement
    from livery.toolroom.store._lock import resolve_lock as resolve_lock
    from livery.toolroom.store._record import ARCHES as ARCHES
    from livery.toolroom.store._record import ARCHIVE_SUFFIXES as ARCHIVE_SUFFIXES
    from livery.toolroom.store._record import DOWNLOAD_KINDS as DOWNLOAD_KINDS
    from livery.toolroom.store._record import FORMATS as FORMATS
    from livery.toolroom.store._record import HOSTS as HOSTS
    from livery.toolroom.store._record import KINDS as KINDS
    from livery.toolroom.store._record import LAYOUT_KEYS as LAYOUT_KEYS
    from livery.toolroom.store._record import MODES as MODES
    from livery.toolroom.store._record import OPTION_KEYS as OPTION_KEYS
    from livery.toolroom.store._record import PACKAGE_VAR as PACKAGE_VAR
    from livery.toolroom.store._record import PLATFORMS as PLATFORMS
    from livery.toolroom.store._record import RECORD_SUFFIX as RECORD_SUFFIX
    from livery.toolroom.store._record import RUNTIMES as RUNTIMES
    from livery.toolroom.store._record import SURFACE_PLATFORMS as SURFACE_PLATFORMS
    from livery.toolroom.store._record import VERB_KEYS as VERB_KEYS
    from livery.toolroom.store._record import VERSION_VAR as VERSION_VAR
    from livery.toolroom.store._record import Artifact as Artifact
    from livery.toolroom.store._record import Delta as RecordDelta
    from livery.toolroom.store._record import Deployment as Deployment
    from livery.toolroom.store._record import Layout as Layout
    from livery.toolroom.store._record import Observation as Observation
    from livery.toolroom.store._record import Record as Record
    from livery.toolroom.store._record import RecordError as RecordError
    from livery.toolroom.store._record import Surface as Surface
    from livery.toolroom.store._record import artifact_format as artifact_format
    from livery.toolroom.store._record import class_name as class_name
    from livery.toolroom.store._record import default_mode as default_mode
    from livery.toolroom.store._record import export_schema as export_schema
    from livery.toolroom.store._record import host_key as host_key
    from livery.toolroom.store._record import observations as observations
    from livery.toolroom.store._record import records_in as records_in
    from livery.toolroom.store._record import resolve as resolve
    from livery.toolroom.store._record import schema as schema
    from livery.toolroom.store._record import surface_at as surface_at
    from livery.toolroom.store._record import validate as validate
    from livery.toolroom.store._record import version_key as version_key
    from livery.toolroom.store._spec import Option as Option
    from livery.toolroom.store._spec import ToolSpec as ToolSpec
    from livery.toolroom.store._spec import Verb as Verb
    from livery.toolroom.store._stub import NameCollision as NameCollision
    from livery.toolroom.store._stub import render as render
    from livery.toolroom.store._stub import render_observation as render_observation
    from livery.toolroom.store._stub import spec_from as spec_from

__all__ = [
    "ARCHES",
    "ARCHIVE_SUFFIXES",
    "BUILD_FILE",
    "DOWNLOAD_KINDS",
    "FORMATS",
    "GRAPHS",
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
    "RECORD_SUFFIX",
    "RUNTIMES",
    "SURFACE_PLATFORMS",
    "TOOLS",
    "URLS",
    "VERB_KEYS",
    "VERSION_VAR",
    "Artifact",
    "Catalogue",
    "CatalogueError",
    "Delta",
    "Deployment",
    "Ensured",
    "Event",
    "FetchError",
    "Fetched",
    "Graph",
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
    "UnpackError",
    "Verb",
    "__version__",
    "api_headers",
    "artifact_format",
    "build_current",
    "bun_global_project",
    "class_name",
    "default_mode",
    "export_schema",
    "fetch_bytes",
    "fetch_file",
    "fetch_json",
    "host_key",
    "npm_cli",
    "observations",
    "read_pointer",
    "records_in",
    "render",
    "render_observation",
    "resolve",
    "resolve_lock",
    "schema",
    "silent",
    "spec_from",
    "surface_at",
    "tree_fingerprint",
    "unpack",
    "validate",
    "version_key",
]

__version__ = "0.0.0"


# The module and attribute each lazily served name comes from.
_EXPORTS: dict[str, tuple[str, str]] = {
    "ARCHES": ("livery.toolroom.store._record", "ARCHES"),
    "ARCHIVE_SUFFIXES": ("livery.toolroom.store._record", "ARCHIVE_SUFFIXES"),
    "Artifact": ("livery.toolroom.store._record", "Artifact"),
    "BUILD_FILE": ("livery.toolroom.store._catalogue", "BUILD_FILE"),
    "Catalogue": ("livery.toolroom.store._catalogue", "Catalogue"),
    "CatalogueError": ("livery.toolroom.store._catalogue", "CatalogueError"),
    "DOWNLOAD_KINDS": ("livery.toolroom.store._record", "DOWNLOAD_KINDS"),
    "Delta": ("livery.toolroom.store._engine", "Delta"),
    "Deployment": ("livery.toolroom.store._record", "Deployment"),
    "Ensured": ("livery.toolroom.store._engine", "Ensured"),
    "Event": ("livery.toolroom.store._engine", "Event"),
    "FORMATS": ("livery.toolroom.store._record", "FORMATS"),
    "FetchError": ("livery.toolroom.store._fetch", "FetchError"),
    "Fetched": ("livery.toolroom.store._engine", "Fetched"),
    "GRAPHS": ("livery.toolroom.store._lock", "GRAPHS"),
    "Graph": ("livery.toolroom.store._lock", "Graph"),
    "HOSTS": ("livery.toolroom.store._record", "HOSTS"),
    "Home": ("livery.toolroom.store._home", "Home"),
    "KINDS": ("livery.toolroom.store._record", "KINDS"),
    "LAYOUT_KEYS": ("livery.toolroom.store._record", "LAYOUT_KEYS"),
    "LINKS": ("livery.toolroom.store._engine", "LINKS"),
    "LOCK_FILE": ("livery.toolroom.store._lock", "LOCK_FILE"),
    "Layout": ("livery.toolroom.store._record", "Layout"),
    "Listed": ("livery.toolroom.store._catalogue", "Listed"),
    "Lock": ("livery.toolroom.store._lock", "Lock"),
    "LockError": ("livery.toolroom.store._lock", "LockError"),
    "Locked": ("livery.toolroom.store._lock", "Locked"),
    "MODES": ("livery.toolroom.store._record", "MODES"),
    "NameCollision": ("livery.toolroom.store._stub", "NameCollision"),
    "OPTION_KEYS": ("livery.toolroom.store._record", "OPTION_KEYS"),
    "Observation": ("livery.toolroom.store._record", "Observation"),
    "Option": ("livery.toolroom.store._spec", "Option"),
    "PACKAGE_VAR": ("livery.toolroom.store._record", "PACKAGE_VAR"),
    "PLATFORMS": ("livery.toolroom.store._record", "PLATFORMS"),
    "POINTER": ("livery.toolroom.store._catalogue", "POINTER"),
    "Progress": ("livery.toolroom.store._engine", "Progress"),
    "RECORD_SUFFIX": ("livery.toolroom.store._record", "RECORD_SUFFIX"),
    "RUNTIMES": ("livery.toolroom.store._record", "RUNTIMES"),
    "Record": ("livery.toolroom.store._record", "Record"),
    "RecordDelta": ("livery.toolroom.store._record", "Delta"),
    "RecordError": ("livery.toolroom.store._record", "RecordError"),
    "Requirement": ("livery.toolroom.store._lock", "Requirement"),
    "SURFACE_PLATFORMS": ("livery.toolroom.store._record", "SURFACE_PLATFORMS"),
    "Store": ("livery.toolroom.store._engine", "Store"),
    "StoreError": ("livery.toolroom.store._engine", "StoreError"),
    "Surface": ("livery.toolroom.store._record", "Surface"),
    "TOOLS": ("livery.toolroom.store._home", "TOOLS"),
    "ToolSpec": ("livery.toolroom.store._spec", "ToolSpec"),
    "URLS": ("livery.toolroom.store._home", "URLS"),
    "UnpackError": ("livery.toolroom.store._fetch", "UnpackError"),
    "VERB_KEYS": ("livery.toolroom.store._record", "VERB_KEYS"),
    "VERSION_VAR": ("livery.toolroom.store._record", "VERSION_VAR"),
    "Verb": ("livery.toolroom.store._spec", "Verb"),
    "api_headers": ("livery.toolroom.store._fetch", "api_headers"),
    "artifact_format": ("livery.toolroom.store._record", "artifact_format"),
    "build_current": ("livery.toolroom.store._catalogue", "build_current"),
    "bun_global_project": ("livery.toolroom.store._engine", "bun_global_project"),
    "class_name": ("livery.toolroom.store._record", "class_name"),
    "default_mode": ("livery.toolroom.store._record", "default_mode"),
    "export_schema": ("livery.toolroom.store._record", "export_schema"),
    "fetch_bytes": ("livery.toolroom.store._fetch", "fetch_bytes"),
    "fetch_file": ("livery.toolroom.store._fetch", "fetch_file"),
    "fetch_json": ("livery.toolroom.store._fetch", "fetch_json"),
    "host_key": ("livery.toolroom.store._record", "host_key"),
    "npm_cli": ("livery.toolroom.store._engine", "npm_cli"),
    "observations": ("livery.toolroom.store._record", "observations"),
    "read_pointer": ("livery.toolroom.store._catalogue", "read_pointer"),
    "records_in": ("livery.toolroom.store._record", "records_in"),
    "render": ("livery.toolroom.store._stub", "render"),
    "render_observation": ("livery.toolroom.store._stub", "render_observation"),
    "resolve": ("livery.toolroom.store._record", "resolve"),
    "resolve_lock": ("livery.toolroom.store._lock", "resolve_lock"),
    "schema": ("livery.toolroom.store._record", "schema"),
    "silent": ("livery.toolroom.store._engine", "silent"),
    "spec_from": ("livery.toolroom.store._stub", "spec_from"),
    "surface_at": ("livery.toolroom.store._record", "surface_at"),
    "tree_fingerprint": ("livery.toolroom.store._fingerprint", "tree_fingerprint"),
    "unpack": ("livery.toolroom.store._fetch", "unpack"),
    "validate": ("livery.toolroom.store._record", "validate"),
    "version_key": ("livery.toolroom.store._record", "version_key"),
}


def __getattr__(name: str) -> object:
    """Serve a public name from its module on first use, then keep it."""
    found = _EXPORTS.get(name)
    if found is None:
        raise AttributeError(
            f"module 'livery.toolroom.store' has no attribute {name!r}"
        )
    import importlib

    module, attribute = found
    value = getattr(importlib.import_module(module), attribute)
    globals()[name] = value
    return value
