"""The package skeleton holds together."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

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


def test_the_workshop_wheel_ships_the_base_alone(tmp_path: Path) -> None:
    import tomllib
    import zipfile
    from importlib.metadata import distribution

    import livery.toolroom.tools as tools

    # It declares no extension: every extension ships in a distribution
    # of its own, and the wheel's one footman plugin is the base's.
    entries = distribution("livery-workshop").entry_points
    assert [entry for entry in entries if entry.group == "workshop.extensions"] == []
    plugins = [entry.name for entry in entries if entry.group == "footman.tasks"]
    assert plugins == ["livery.workshop"]
    # What an extension that left needed goes with it: griffelib, which
    # the API reference's check reads sources with, and Pygments and its
    # stubs, which the coverage pages highlight with, are none of the
    # wheel's requirements, in any extra.
    required = distribution("livery-workshop").requires or []
    assert [
        line for line in required if "griffelib" in line or "pygments" in line
    ] == []
    # The build ships the base's root alone, which has its __init__.py,
    # so the build takes no namespace flag and refuses a root that
    # lacks one.
    member = Path(__file__).resolve().parents[1]
    project = tomllib.loads((member / "pyproject.toml").read_text("utf-8"))
    backend = project["tool"]["uv"]["build-backend"]
    assert backend == {"module-name": "livery.workshop"}
    # The wheel the build writes holds livery/workshop/ and its metadata,
    # and nothing else.
    built = tools.uv.opts(cwd=member, nofail=True, recorded=False)(
        "build", "--wheel", "--out-dir", str(tmp_path)
    )
    assert built.code == 0, built.stdout + built.stderr
    (wheel,) = tmp_path.glob("*.whl")
    with zipfile.ZipFile(wheel) as archive:
        roots = {
            "/".join(entry.filename.split("/")[:2])
            for entry in archive.infolist()
            if not entry.is_dir() and ".dist-info/" not in entry.filename
        }
    assert roots == {"livery/workshop"}


def test_the_surface_is_declared() -> None:
    assert workshop_module.__all__ == [
        "AGENT",
        "COMPILE_COMMANDS",
        "CURRENT_VERSION",
        "DISTRIBUTIONS",
        "DURATIONS",
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
        "Shard",
        "ShardMarker",
        "SourceReferences",
        "SpeedMark",
        "ToolReceipt",
        "VerifiedBranch",
        "__version__",
        "answer",
        "build_release",
        "category_of",
        "check_legs",
        "check_shards",
        "checkout_time",
        "ci_changes",
        "ci_run",
        "contributions_for",
        "discover_packages",
        "discover_release",
        "extension_names",
        "extension_set",
        "files_digest",
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
        "leave_shard",
        "leg_scope",
        "nearest_extensions",
        "neighbours",
        "order_topologically",
        "partition",
        "platform_leg",
        "pull_request_legs",
        "pull_request_shards",
        "python_matrix",
        "read_contract",
        "read_durations",
        "read_shard",
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
        "shard_of",
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
