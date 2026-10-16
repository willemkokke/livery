"""The coverage extension's own distribution: its entry points, its plugin, the kit.

Then what a workspace without it installs, and the ignore lines its
content ships.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from extensions_coverage_workspace import (
    composed_file,
    composed_groups,
    skip_unless_installed,
)
from livery.workshop.testing import CLAUSES, Clause


def test_its_own_distribution_ships_coverage_and_the_workshop_wheel_none() -> None:
    from importlib.metadata import distribution, requires

    from livery.workshop._extensions import declaration, shipping_distribution

    ours = {
        (entry.group, entry.name, entry.value)
        for entry in distribution("livery-extensions-coverage").entry_points
    }
    assert ours == {
        ("workshop.extensions", "coverage", "livery.extensions.coverage"),
        (
            "footman.tasks",
            "livery.extensions.coverage",
            "livery.extensions.coverage._tasks",
        ),
    }
    theirs = distribution("livery-workshop").entry_points
    assert [
        entry.name
        for entry in theirs
        if entry.value.startswith("livery.extensions.coverage")
    ] == []
    shipped = shipping_distribution("livery.extensions.coverage")
    assert shipped is not None and shipped.name == "livery-extensions-coverage"
    declared = declaration("coverage")
    assert declared is not None and declared.requires == ()
    # The pages highlight with Pygments, which this wheel requires and
    # the workshop's no longer does.
    assert any(
        line.startswith("pygments")
        for line in requires("livery-extensions-coverage") or ()
    )
    assert [
        line for line in requires("livery-workshop") or () if "pygments" in line
    ] == []


def test_the_plugin_loaded_alone_registers_the_coverage_group_alone() -> None:
    # The task reference renders each provider's tree with nothing else
    # mounted: the plugin's tree holds the coverage group and no group of
    # the base's, with the leg's entry and the union hidden.
    probe = (
        "import json\n"
        "import livery.footman as footman\n"
        "import livery.extensions.coverage._tasks\n"
        "tree = footman.task_tree()\n"
        "group = tree['groups']['coverage']\n"
        "print(json.dumps([sorted(tree['groups']), sorted(tree['tasks']),"
        " sorted(group['tasks'])]))\n"
    )
    run = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=False
    )
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout) == [
        ["coverage"],
        [],
        ["accept", "enforce", "leg", "union"],
    ]


def _workspace(tmp_path: Path, listed: str) -> Path:
    """A workspace listing *listed*, with the python member core."""
    root = tmp_path / "ws"
    member = root / "packages" / "core"
    (member / "src" / "acme" / "core").mkdir(parents=True)
    (member / "src" / "acme" / "core" / "__init__.py").write_text("")
    (member / "workshop.toml").write_text(
        'extensions = ["python"]\nname = "acme-core"\n'
    )
    (member / "pyproject.toml").write_text('[project]\nname = "acme-core"\n')
    (root / "workshop.toml").write_text(
        f'[workspace]\nname = "acme"\nextensions = {listed}\n'
    )
    (root / "pyproject.toml").write_text('[project]\nname = "acme-home"\n')
    return root


def _mount(root: Path) -> None:
    """Mount what *root* lists, so its steps register as a verb finds them."""
    from livery.footman import _registry as registry
    from livery.workshop._extensions import mount_extensions

    with registry.capture():
        mount_extensions(root)


def test_a_workspace_without_coverage_installs_none(tmp_path: Path) -> None:
    # Unlisted, neither the extension's distribution nor its highlighter
    # reaches the venv: no line of any group the project file composes
    # names either, and the workshop wheel requires neither.
    from importlib.metadata import requires

    root = _workspace(tmp_path, "[]")
    lines = [line for group in composed_groups(root).values() for line in group]
    assert [
        line
        for line in lines
        if "livery-extensions-coverage" in line or "pygments" in line
    ] == []
    required = requires("livery-workshop") or []
    assert [
        line for line in required if "extensions-coverage" in line or "pygments" in line
    ] == []
    # Listed, the dev group installs the extension's distribution.
    root = _workspace(tmp_path / "listed", '["coverage"]')
    assert "livery-extensions-coverage" in composed_groups(root)["dev"]


def test_a_workspace_listing_pytest_without_coverage_installs_no_meter(
    tmp_path: Path,
) -> None:
    # pytest measures for coverage through its [for.coverage] table, so
    # the meter joins the dev group only while coverage is listed too.
    skip_unless_installed("pytest")

    def meters(root: Path) -> list[str]:
        return sorted(
            line
            for line in composed_groups(root)["dev"]
            if line.startswith(("coverage", "pytest-cov"))
        )

    assert meters(_workspace(tmp_path / "alone", '["pytest"]')) == []
    measured = _workspace(tmp_path / "measured", '["coverage", "pytest"]')
    assert meters(measured) == ["coverage>=7.13", "pytest-cov>=5"]


@pytest.mark.parametrize("clause", CLAUSES, ids=lambda clause: clause.name)
def test_coverage_conforms(clause: Clause, tmp_path: Path) -> None:
    from livery.workshop._steps import StepRecord, steps_by_name
    from livery.workshop.testing import Subject

    _mount(_workspace(tmp_path, '["coverage"]'))
    # Listed alone, coverage registers no step: its steps join the site's
    # phases while docs is listed.
    steps: tuple[StepRecord, ...] = tuple(
        record for record in steps_by_name().values() if record.extension == "coverage"
    )
    assert steps == ()
    subject = Subject("livery.extensions.coverage", sets=(("python",),))
    assert clause.judge(subject) == []


def test_every_coverage_data_file_is_ignored_and_its_configuration_tracked(
    tmp_path: Path,
) -> None:
    # A gate writes the data at the root, and a file it leaves untracked
    # makes the tree dirty, which a workflow verb refuses; coverage's own
    # configuration, `.coveragerc`, is a tracked file beside them. The
    # extensions that write the data ship its lines: the meter's data is
    # pytest's, while it measures for coverage, the rest coverage's, and
    # the base names none of them.
    from livery.extensions.coverage._parts import PARTS, UNION

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    metered = (".coverage", ".coverage.host.1.2")
    data = (*metered, "packages/geometry/htmlcov/index.html")
    # The parts a measurer leaves and the union the gate job writes live
    # under .workshop/, which the base ignores in every workspace.
    held = (f"{PARTS}/gcov/packages-geometry.json", UNION)
    for name in (*data, *held, ".coveragerc"):
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text("x\n")

    def ignored(name: str) -> bool:
        done = subprocess.run(["git", "check-ignore", "-q", name], cwd=repo)
        return done.returncode == 0

    (repo / ".gitignore").write_text(composed_file(tmp_path, ".gitignore"))
    assert [name for name in data if ignored(name)] == []
    assert [name for name in held if not ignored(name)] == []
    # The pytest extension is a distribution of its own: a run of this
    # wheel alone has none, and the rest of the test skips.
    skip_unless_installed("pytest")
    # Listed alone, pytest runs no meter and leaves no data to ignore.
    (repo / ".gitignore").write_text(composed_file(tmp_path, ".gitignore", ("pytest",)))
    assert [name for name in data if ignored(name)] == []
    listed = ("pytest", "coverage")
    (repo / ".gitignore").write_text(composed_file(tmp_path, ".gitignore", listed))
    assert [name for name in (*data, *held) if not ignored(name)] == []
    assert not ignored(".coveragerc")
