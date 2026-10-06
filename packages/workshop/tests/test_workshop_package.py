"""The package skeleton holds together."""

from __future__ import annotations

import pytest

import livery.forge.api as forge_module
import livery.workshop.api as workshop_module


def test_imports_and_carries_a_version() -> None:
    from importlib.metadata import version

    # Against the installed metadata, never a literal: the release
    # train stamps the version, and a spelled copy here would need a
    # hand edit every release.
    assert workshop_module.__version__ == version("livery-workshop")


def test_the_namespace_is_shared_and_stays_pep420() -> None:
    # Both distributions serve one namespace; an __init__.py at
    # livery/ in either wheel would break the other.
    import livery

    assert forge_module.__name__ == "livery.forge.api"
    assert workshop_module.__name__ == "livery.workshop.api"
    assert getattr(livery, "__file__", None) is None


def test_the_surface_is_declared() -> None:
    assert workshop_module.__all__ == [
        "PACKAGE",
        "PACKAGES",
        "PATHS",
        "WHOLE",
        "CheckRecord",
        "Claim",
        "Edge",
        "Fragment",
        "GateContext",
        "Option",
        "Package",
        "__version__",
        "check_option",
        "compile_commands",
        "discover_packages",
        "extension_names",
        "kind_examples",
        "mount_extensions",
        "public_modules",
        "rewrite_nav_block",
        "run_batched",
        "run_suites",
        "scoped_files",
        "scoped_packages",
        "scoped_paths",
        "testing",
        "verify_workspace",
        "workspace_root",
        "workspace_suite",
    ]


def test_the_api_serves_its_testing_kit_on_first_use_and_refuses_an_unknown_name() -> (
    None
):
    import importlib

    assert workshop_module.testing is importlib.import_module("livery.workshop.testing")
    with pytest.raises(AttributeError, match="has no attribute 'nothing_here'"):
        workshop_module.nothing_here  # noqa: B018  # pyright: ignore[reportAttributeAccessIssue]
