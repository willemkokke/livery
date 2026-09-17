"""The store's package contract: its surface and its version."""

from __future__ import annotations

from importlib.metadata import version

import livery.toolroom.store as package


def test_the_version_is_the_installed_distributions() -> None:
    assert package.__version__ == version("livery-toolroom-store")


def test_the_public_surface_is_pinned() -> None:
    assert set(package.__all__) == {
        "ARCHES",
        "DELTAS_DIR",
        "DOWNLOAD_KINDS",
        "HOSTS",
        "KINDS",
        "LAYOUT_KEYS",
        "OPTION_KEYS",
        "PACKAGE_VAR",
        "PLATFORMS",
        "POINTER",
        "SURFACE_PLATFORMS",
        "TOOL_FILE",
        "VERB_KEYS",
        "LINKS",
        "LOCK_FILE",
        "MODES",
        "TOOLS",
        "URLS",
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
        "Observation",
        "Progress",
        "Record",
        "RecordDelta",
        "RecordError",
        "Requirement",
        "Store",
        "StoreError",
        "Surface",
        "__version__",
        "default_mode",
        "export_schema",
        "host_key",
        "observations",
        "resolve",
        "resolve_lock",
        "schema",
        "silent",
        "surface_at",
        "validate",
        "version_key",
    }
