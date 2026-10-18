"""What a change to a module pytest loads as a plugin reaches: every member pytest runs.

The fallbacks first: a change python's answer does not widen, and a
manifest it cannot read. Then each change it widens: a registered
module, a file under a registered package, a registered module the
change removed, and a ``pytest11`` table that changed.
"""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

import pytest

from extensions_python_workspace import skip_unless_installed
from livery.extensions.python._backend import plugin_reaches
from livery.workshop import Changes

#: The members pytest runs over in the workspace below: every python
#: member that keeps a pytest step on, in discovery order.
SUITES = ("packages/host", "packages/plain", "packages/tasks")

#: The host's two plugins: a module, and a package.
HOST_PLUGINS = (
    '[project.entry-points.pytest11]\nhost = "acme.host._plugin"\n'
    'deep = "acme.host._deep"\n'
)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=True)


def _member(root: Path, name: str, *, extensions: str, project: str = "") -> Path:
    """The member ``packages/<name>`` listing *extensions*; its directory."""
    directory = root / "packages" / name
    directory.mkdir(parents=True)
    (directory / "workshop.toml").write_text(
        f'extensions = {extensions}\nname = "acme-{name}"\n'
    )
    if extensions != "[]":
        (directory / "pyproject.toml").write_text(
            f'[project]\nname = "acme-{name}"\ndependencies = []\n\n{project}'
        )
    return directory


def _write(directory: Path, *files: str) -> None:
    for relative in files:
        path = directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x = 1\n")


@pytest.fixture
def work(tmp_path: Path) -> Path:
    """A committed workspace whose ``host`` member names two pytest plugins.

    Beside it: ``tasks`` names a module under ``footman.tasks`` alone,
    ``plain`` names none, ``quiet`` turns both pytest steps off, and
    ``bare`` lists no extension.
    """
    skip_unless_installed("pytest")
    root = tmp_path / "ws"
    root.mkdir()
    (root / "workshop.toml").write_text("[workspace]\n")
    host = _member(root, "host", extensions='["python"]', project=HOST_PLUGINS)
    _write(
        host,
        "src/acme/host/_plugin.py",
        "src/acme/host/_deep/__init__.py",
        "src/acme/host/_deep/fixtures.py",
        "src/acme/host/other.py",
        "tests/test_host.py",
    )
    tasks = _member(
        root,
        "tasks",
        extensions='["python"]',
        project='[project.entry-points."footman.tasks"]\ntasks = "acme.tasks._tasks"\n',
    )
    _write(tasks, "src/acme/tasks/_tasks.py")
    _write(_member(root, "plain", extensions='["python"]'), "src/acme/plain/mod.py")
    quiet = _member(root, "quiet", extensions='["python"]')
    with (quiet / "workshop.toml").open("a") as contract:
        contract.write(
            "\n[phases.test.pytest]\nenabled = false\n"
            "\n[phases.examples.pytest]\nenabled = false\n"
        )
    _member(root, "bare", extensions="[]")
    _git(root, "init", "-q", "--initial-branch=main")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "chore: seed")
    return root


def _reaches(root: Path, *paths: str) -> dict[str, tuple[str, tuple[str, ...]]]:
    return plugin_reaches(Changes(root, paths, "HEAD"))


def test_a_change_to_no_plugin_of_pytest_widens_nothing(work: Path) -> None:
    assert _reaches(work, "packages/plain/src/acme/plain/mod.py") == {}
    # A module the runner loads, and pytest does not.
    assert _reaches(work, "packages/tasks/src/acme/tasks/_tasks.py") == {}
    # A host's other module, its tests, and a file outside every member.
    assert _reaches(work, "packages/host/src/acme/host/other.py") == {}
    assert _reaches(work, "packages/host/tests/test_host.py") == {}
    assert _reaches(work, "tasks.py", "notes/plan.md") == {}
    # A name that starts like a plugin's is another module.
    assert _reaches(work, "packages/host/src/acme/host/_plugin_helpers.py") == {}


def test_a_manifest_change_outside_the_plugin_tables_widens_nothing(work: Path) -> None:
    manifest = work / "packages" / "host" / "pyproject.toml"
    manifest.write_text(
        manifest.read_text().replace("dependencies = []", 'dependencies = ["attrs"]')
    )
    assert _reaches(work, "packages/host/pyproject.toml") == {}
    tasks = work / "packages" / "tasks" / "pyproject.toml"
    tasks.write_text(tasks.read_text().replace("_tasks", "_more_tasks"))
    assert _reaches(work, "packages/tasks/pyproject.toml") == {}


def test_a_manifest_that_is_not_toml_raises(work: Path) -> None:
    # The affected engine runs everything on a raise, naming the error.
    (work / "packages" / "host" / "pyproject.toml").write_text("[project\n")
    with pytest.raises(tomllib.TOMLDecodeError):
        _reaches(work, "packages/host/pyproject.toml")


def test_a_registered_module_reaches_every_member_pytest_runs(work: Path) -> None:
    path = "packages/host/src/acme/host/_plugin.py"
    assert _reaches(work, path) == {path: ("pytest loads it as a plugin", SUITES)}


def test_a_file_under_a_registered_package_reaches_every_member_pytest_runs(
    work: Path,
) -> None:
    package = "packages/host/src/acme/host/_deep/__init__.py"
    module = "packages/host/src/acme/host/_deep/fixtures.py"
    found = _reaches(work, package, module, "packages/plain/src/acme/plain/mod.py")
    assert found == {
        package: ("pytest loads it as a plugin", SUITES),
        module: ("pytest loads it with the plugin acme.host._deep", SUITES),
    }


def test_a_registered_module_the_change_removed_reaches_every_member(
    work: Path,
) -> None:
    host = work / "packages" / "host"
    (host / "src/acme/host/_plugin.py").unlink()
    (host / "src/acme/host/_deep/fixtures.py").unlink()
    (host / "pyproject.toml").write_text(
        '[project]\nname = "acme-host"\ndependencies = []\n'
    )
    module = "packages/host/src/acme/host/_plugin.py"
    under = "packages/host/src/acme/host/_deep/fixtures.py"
    manifest = "packages/host/pyproject.toml"
    assert _reaches(work, module, under, manifest) == {
        module: ("pytest loaded it as a plugin", SUITES),
        under: ("pytest loaded it with the plugin acme.host._deep", SUITES),
        manifest: ("the plugins it names for pytest changed", SUITES),
    }
    # Without the earlier tree, a removed module is no plugin.
    assert plugin_reaches(Changes(work, (module,), "")) == {}


def test_a_plugin_table_that_moved_reaches_every_member_from_both_manifests(
    work: Path,
) -> None:
    host = work / "packages" / "host" / "pyproject.toml"
    plain = work / "packages" / "plain" / "pyproject.toml"
    host.write_text(host.read_text().replace(HOST_PLUGINS, ""))
    plain.write_text(plain.read_text() + HOST_PLUGINS.replace("host.", "plain."))
    why = "the plugins it names for pytest changed"
    assert _reaches(work, "packages/host/pyproject.toml") == {
        "packages/host/pyproject.toml": (why, SUITES)
    }
    assert _reaches(work, "packages/plain/pyproject.toml") == {
        "packages/plain/pyproject.toml": (why, SUITES)
    }
