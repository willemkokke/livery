"""The package skeleton holds together."""

from __future__ import annotations

import pytest

import livery.forge as forge_module
import livery.workshop as workshop_module


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

    assert forge_module.__name__ == "livery.forge"
    assert workshop_module.__name__ == "livery.workshop"
    assert getattr(livery, "__file__", None) is None


def test_the_surface_is_declared() -> None:
    assert workshop_module.__all__ == [
        "AGENT",
        "HUMAN",
        "NONE",
        "PACKAGE",
        "PACKAGES",
        "PATHS",
        "WHOLE",
        "Changes",
        "Edge",
        "GateContext",
        "Package",
        "Prose",
        "RegistryTarget",
        "ReleaseNotes",
        "RunContext",
        "__version__",
        "check_option",
        "ci_changes",
        "ci_run",
        "compile_commands",
        "contributions_for",
        "discover_packages",
        "extension_names",
        "forge_repository",
        "generated_header",
        "guidance",
        "kind_examples",
        "public_modules",
        "read_contract",
        "registry",
        "release_notes",
        "run_batched",
        "run_suites",
        "scoped_files",
        "scoped_packages",
        "scoped_paths",
        "selected_files",
        "slot",
        "testing",
        "this_forge",
        "this_repository",
        "verify_workspace",
        "workspace_root",
        "workspace_suite",
    ]


def test_the_workshop_serves_no_nav_block_helper() -> None:
    # A generator writes its nav block as data, the file the docs
    # extension reads; neither the workshop nor the extension serves code
    # for it.
    with pytest.raises(AttributeError, match="rewrite_nav_block"):
        workshop_module.rewrite_nav_block  # noqa: B018  # pyright: ignore[reportAttributeAccessIssue]


def test_the_api_serves_its_testing_kit_on_first_use_and_refuses_an_unknown_name() -> (
    None
):
    import importlib

    assert workshop_module.testing is importlib.import_module("livery.workshop.testing")
    with pytest.raises(AttributeError, match="has no attribute 'nothing_here'"):
        workshop_module.nothing_here  # noqa: B018  # pyright: ignore[reportAttributeAccessIssue]
