"""The store's package contract: its surface and its version."""

from __future__ import annotations

from importlib.metadata import version

import livery.toolroom.store as package


def test_the_version_is_the_installed_distributions() -> None:
    assert package.__version__ == version("livery-toolroom-store")


def test_the_public_surface_is_pinned() -> None:
    assert set(package.__all__) == {
        "ARCHES",
        "DOWNLOAD_KINDS",
        "HOSTS",
        "KINDS",
        "PACKAGE_VAR",
        "PLATFORMS",
        "LINKS",
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
    }
