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


#: The extensions the workshop's wheel ships beside the base, each by the
#: name a contract lists to the package it mounts from. Each extension
#: that leaves the wheel for a distribution of its own leaves this pin.
SHIPPED = {
    "conan": "livery.extensions.conan",
    "coverage": "livery.extensions.coverage",
    "cpp": "livery.extensions.cpp",
    "nanobind": "livery.extensions.nanobind",
    "python": "livery.extensions.python",
}


def test_the_workshop_wheel_ships_the_base_and_these_extensions() -> None:
    import tomllib
    from importlib.metadata import distribution
    from pathlib import Path

    declared = {
        entry.name: entry.value
        for entry in distribution("livery-workshop").entry_points
        if entry.group == "workshop.extensions"
    }
    assert declared == SHIPPED
    # What an extension that left needed goes with it: griffelib, which
    # the API reference's check reads sources with, is none of the
    # wheel's requirements, in any extra.
    required = distribution("livery-workshop").requires or []
    assert [line for line in required if "griffelib" in line] == []
    # The build ships the base's root and each of theirs, and no other.
    project = tomllib.loads(
        (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text("utf-8")
    )
    roots = project["tool"]["uv"]["build-backend"]["module-name"]
    assert roots == ["livery.workshop", *sorted(SHIPPED.values())]


def test_the_surface_is_declared() -> None:
    assert workshop_module.__all__ == [
        "AGENT",
        "COMPILE_COMMANDS",
        "CURRENT_VERSION",
        "DISTRIBUTIONS",
        "EXECUTABLES",
        "HUMAN",
        "JOB_BUILT_MEMBERS",
        "MODULE_ROOTS",
        "NONE",
        "PACKAGE",
        "PACKAGES",
        "PATHS",
        "PUBLIC_MODULES",
        "REQUIREMENTS",
        "RUNS",
        "VERSION_FILES",
        "WHOLE",
        "WORKSPACE_TESTS",
        "Category",
        "Changes",
        "Edge",
        "LegScope",
        "Package",
        "PhaseContext",
        "Prose",
        "Query",
        "RegistryTarget",
        "ReleaseNotes",
        "RunContext",
        "ToolReceipt",
        "VerifiedBranch",
        "__version__",
        "answer",
        "build_release",
        "category_of",
        "check_legs",
        "ci_changes",
        "ci_run",
        "contributions_for",
        "discover_packages",
        "discover_release",
        "extension_names",
        "extension_set",
        "fix_files",
        "forge_repository",
        "forge_token",
        "generated_header",
        "guidance",
        "holds_runtime_language",
        "leave_leg_file",
        "leave_section",
        "leg_scope",
        "nearest_extensions",
        "order_topologically",
        "pull_request_legs",
        "python_matrix",
        "read_contract",
        "registry",
        "release_notes",
        "release_tag",
        "run_batched",
        "run_phase",
        "run_phase_over",
        "scoped_files",
        "scoped_packages",
        "scoped_paths",
        "scoped_suite",
        "selected_files",
        "shipped_content",
        "state_store",
        "step_enabled",
        "step_extensions",
        "step_option",
        "store_home",
        "tested",
        "testing",
        "this_forge",
        "this_repository",
        "tool_receipts",
        "undeployed",
        "verified_branch",
        "verify_workspace",
        "workspace_root",
        "workspace_suite",
    ]


def test_the_workshop_serves_no_nav_block_helper() -> None:
    # A generator writes its nav block as data, the file the docs
    # extension reads; the workshop serves no code for it.
    with pytest.raises(AttributeError, match="rewrite_nav_block"):
        workshop_module.rewrite_nav_block  # noqa: B018  # pyright: ignore[reportAttributeAccessIssue]


def test_the_api_serves_its_testing_kit_on_first_use_and_refuses_an_unknown_name() -> (
    None
):
    import importlib

    assert workshop_module.testing is importlib.import_module("livery.workshop.testing")
    with pytest.raises(AttributeError, match="has no attribute 'nothing_here'"):
        workshop_module.nothing_here  # noqa: B018  # pyright: ignore[reportAttributeAccessIssue]


def test_the_python_and_conan_entry_modules_refuse_a_name_they_do_not_export() -> None:
    import importlib

    # Each refused name is a function of the module that defines the
    # exported ones: only the declared names are served.
    for root, name in (
        ("livery.extensions.python", "materialise_module_docs"),
        ("livery.extensions.conan", "restore_cache"),
    ):
        module = importlib.import_module(root)
        with pytest.raises(
            AttributeError, match=f"module '{root}' has no attribute '{name}'"
        ):
            getattr(module, name)


def test_an_entry_module_serves_what_the_defining_module_holds_at_each_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import livery.extensions.conan as conan_module
    import livery.extensions.python as python_module
    from livery.extensions.conan import _package
    from livery.extensions.python import _backend

    # A test patches the defining module, and a reader of the entry
    # module sees the patch.
    saved, tested = object(), object()
    monkeypatch.setattr(_package, "save_cache", saved)
    monkeypatch.setattr(_backend, "run_isolated_test", tested)
    assert conan_module.save_cache is saved
    assert python_module.run_isolated_test is tested
    monkeypatch.undo()
    assert conan_module.save_cache is _package.save_cache
    assert python_module.run_isolated_test is _backend.run_isolated_test
