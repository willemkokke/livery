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
        "PACKAGE_VAR",
        "PLATFORMS",
        "TOOL_FILE",
        "LINKS",
        "TOOLS",
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
    }
