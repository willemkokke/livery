"""The package skeleton holds together."""

from __future__ import annotations

import re
import subprocess
import sys

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


#: The extension the workshop's wheel ships beside the base, by the name
#: a contract lists to the package it mounts from: python alone. Each
#: extension that leaves the wheel for a distribution of its own leaves
#: this pin.
SHIPPED = {
    "python": "livery.extensions.python",
}


def test_the_workshop_wheel_ships_the_base_and_the_python_extension() -> None:
    import tomllib
    from importlib.metadata import distribution
    from pathlib import Path

    declared = {
        entry.name: entry.value
        for entry in distribution("livery-workshop").entry_points
        if entry.group == "workshop.extensions"
    }
    assert declared == SHIPPED
    # The extension it ships carries no plugin: the wheel's one footman
    # plugin is the base's.
    plugins = [
        entry.name
        for entry in distribution("livery-workshop").entry_points
        if entry.group == "footman.tasks"
    ]
    assert plugins == ["livery.workshop"]
    # What an extension that left needed goes with it: griffelib, which
    # the API reference's check reads sources with, and Pygments and its
    # stubs, which the coverage pages highlight with, are none of the
    # wheel's requirements, in any extra.
    required = distribution("livery-workshop").requires or []
    assert [
        line for line in required if "griffelib" in line or "pygments" in line
    ] == []
    # The build ships the base's root and python's, and no other.
    project = tomllib.loads(
        (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text("utf-8")
    )
    backend = project["tool"]["uv"]["build-backend"]
    assert backend["module-name"] == ["livery.workshop", *sorted(SHIPPED.values())]
    # Each root has its __init__.py, so the build takes no namespace
    # flag and refuses a root that lacks one.
    assert "namespace" not in backend


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
        "SPEED_FILE_VARIABLE",
        "VERSION_FILES",
        "WHOLE",
        "WORKSPACE_TESTS",
        "Category",
        "Changes",
        "Edge",
        "LegScope",
        "Neighbours",
        "Package",
        "PhaseContext",
        "Prose",
        "Query",
        "ReferencedName",
        "RegistryTarget",
        "ReleaseNotes",
        "RequirementWriter",
        "RunContext",
        "SourceReferences",
        "SpeedMark",
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
        "graph_changed",
        "guidance",
        "holds_runtime_language",
        "is_example",
        "leave_leg_file",
        "leave_section",
        "leg_scope",
        "nearest_extensions",
        "neighbours",
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
        "scripts_dir",
        "selected_files",
        "shipped_content",
        "speed_marks",
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
        "venv_python",
        "verified_branch",
        "verify_workspace",
        "workspace_root",
        "workspace_suite",
    ]


#: The names an extension that runs python and judges its sources reads
#: of the workshop. The workshop does not depend on pytest, so none of
#: them may need it.
PYTHON_READS = (
    "Neighbours",
    "ReferencedName",
    "SPEED_FILE_VARIABLE",
    "SourceReferences",
    "SpeedMark",
    "graph_changed",
    "is_example",
    "neighbours",
    "scripts_dir",
    "speed_marks",
    "venv_python",
)

#: A fresh interpreter's preamble: every import of pytest fails, and
#: what the interpreter's start-up loaded of it is forgotten first.
_WITHOUT_PYTEST = """\
import importlib.abc, sys

BLOCKED = ("pytest", "_pytest")


class Blocked(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.partition(".")[0] in BLOCKED:
            raise ImportError(f"{name} is blocked")
        return None


for loaded in [m for m in sys.modules if m.partition(".")[0] in BLOCKED]:
    del sys.modules[loaded]
sys.meta_path.insert(0, Blocked())
"""


def _without_pytest(body: str) -> subprocess.CompletedProcess[str]:
    """Run *body* in a fresh interpreter in which importing pytest fails."""
    return subprocess.run(
        [sys.executable, "-c", _WITHOUT_PYTEST + body],
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_names_python_reads_need_no_pytest() -> None:
    # The block bites first: a pytest plugin of the workshop cannot load
    # in that interpreter, so a name served from one would fail there.
    refused = _without_pytest("import livery.workshop._pytest_examples\n")
    assert refused.returncode != 0
    assert "ImportError: pytest is blocked" in refused.stderr
    found = _without_pytest(
        "from livery.workshop import SPEED_FILE_VARIABLE, SpeedMark, is_example,"
        " speed_marks\n"
        "import livery.workshop as workshop\n"
        f"for name in {PYTHON_READS!r}:\n"
        "    getattr(workshop, name)\n"
        "print(sorted(m for m in sys.modules if m.partition('.')[0] in BLOCKED))\n"
    )
    assert found.returncode == 0, found.stderr
    assert found.stdout == "[]\n"
    assert set(PYTHON_READS) <= set(workshop_module.__all__)


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


def test_the_python_entry_module_refuses_a_name_it_does_not_export() -> None:
    import livery.extensions.python as python_module

    # The refused name is a function of the module that defines the
    # exported ones: only the declared names are served.
    with pytest.raises(
        AttributeError,
        match=re.escape(
            "module 'livery.extensions.python' has no attribute"
            " 'materialise_module_docs'"
        ),
    ):
        python_module.materialise_module_docs  # noqa: B018  # pyright: ignore[reportAttributeAccessIssue]


def test_an_entry_module_serves_what_the_defining_module_holds_at_each_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import livery.extensions.python as python_module
    from livery.extensions.python import _backend

    # A test patches the defining module, and a reader of the entry
    # module sees the patch.
    tested = object()
    monkeypatch.setattr(_backend, "run_isolated_test", tested)
    assert python_module.run_isolated_test is tested
    monkeypatch.undo()
    assert python_module.run_isolated_test is _backend.run_isolated_test
