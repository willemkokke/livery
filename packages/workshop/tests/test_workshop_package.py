"""The package skeleton holds together."""

from __future__ import annotations

import livery.forge as forge_module
import livery.workshop as workshop_module


def test_imports_and_carries_a_version() -> None:
    assert workshop_module.__version__ == "0.0.1"


def test_the_namespace_is_shared_and_stays_pep420() -> None:
    # Both distributions serve one namespace; an __init__.py at
    # livery/ in either wheel would break the other.
    import livery

    assert forge_module.__name__ == "livery.forge"
    assert workshop_module.__name__ == "livery.workshop"
    assert getattr(livery, "__file__", None) is None


def test_the_surface_is_declared() -> None:
    assert workshop_module.__all__ == [
        "Edge",
        "Package",
        "__version__",
        "discover_packages",
        "layer_names",
        "mount_layers",
        "verify_workspace",
        "workspace_root",
    ]
