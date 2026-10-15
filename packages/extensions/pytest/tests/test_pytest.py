"""The pytest extension: unlisted it does nothing; listed, it runs the suites.

Its steps run the suites and the examples through the functions the
python extension answers with, and run nothing, saying why, while
nothing answers. Beside coverage it measures them: the configuration,
the meter and the ignore lines arrive with coverage, and a coverage
listing nothing measures for is dormant and named.
"""

from __future__ import annotations

import configparser
import subprocess
import sys
import tomllib
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest

from livery.extensions.python import _backend as _python
from livery.workshop import Package, PhaseContext, Query, answer
from livery.workshop import _steps as registry

CONTRACT = (
    "[workspace]\n"
    'name = "acme"\n'
    'namespace = "acme"\n'
    'authors = [{ name = "Acme", email = "dev@acme.test" }]\n'
    'copyright-year = "2026"\n'
)


@pytest.fixture
def registered() -> Iterator[None]:
    """Pytest's steps registered, as the mount registers a listed extension's."""
    from livery.workshop._extensions import declaration, register_declared

    state = registry.snapshot()
    found = declaration("pytest")
    assert found is not None
    register_declared("pytest", found.additions)
    try:
        yield
    finally:
        registry.restore(state)


def _workspace(root: Path, extensions: str = '["pytest"]') -> None:
    """Write *root*'s contract listing *extensions*, a TOML list as written."""
    (root / "workshop.toml").write_text(CONTRACT + f"extensions = {extensions}\n")


def _member(
    root: Path,
    name: str,
    *,
    serial: bool = False,
    extensions: tuple[str, ...] = ("python",),
) -> Package:
    """A member listing *extensions*, its contract written as the mount reads it.

    *serial* sets its suite's ``parallel`` option off.
    """
    directory = root / "packages" / name
    (directory / "tests").mkdir(parents=True)
    (directory / "docs" / "examples").mkdir(parents=True)
    listed = ", ".join(f'"{extension}"' for extension in extensions)
    (directory / "workshop.toml").write_text(f"extensions = [{listed}]\n")
    options: tuple[tuple[str, tuple[tuple[str, object], ...]], ...] = ()
    if serial:
        options = (("phases.test.pytest", (("parallel", False),)),)
    return Package(
        directory=directory,
        path=f"packages/{name}",
        name=f"acme-{name}",
        depends=(),
        step_options=options,
        extensions=extensions,
    )


#: The dev group's lines, a query the listed extensions answer.
DEV_GROUP: Query[tuple[str, ...]] = Query("python.dev-group")


def _dev_group(root: Path) -> list[str]:
    """The dev group's lines, as the extensions listed at *root* answer them."""
    return list(answer(root, DEV_GROUP))


Call = tuple[tuple[str, ...], dict[str, object]]


def _watch(monkeypatch: pytest.MonkeyPatch) -> list[Call]:
    """Each call the test step makes of python's suite runner.

    The python extension answers with the function it finds on its
    module when asked, so the watcher stands in for it there.
    """
    calls: list[Call] = []

    def run_test(*arguments: str, **options: object) -> None:
        calls.append((arguments, options))

    monkeypatch.setattr(_python, "run_test", run_test)
    return calls


Ran = tuple[str, Path, tuple[str, ...], tuple[str, ...]]


def _watch_examples(monkeypatch: pytest.MonkeyPatch) -> list[Ran]:
    """Each call the examples step makes of python's examples runner."""
    ran: list[Ran] = []

    def run_examples(
        package: Package, root: Path, files: tuple[str, ...], arguments: tuple[str, ...]
    ) -> None:
        ran.append((package.path, root, files, arguments))

    monkeypatch.setattr(_python, "run_examples", run_examples)
    return ran


def _paths(call: Call) -> tuple[str, ...]:
    packages = call[1]["packages"]
    assert isinstance(packages, tuple)
    return tuple(str(getattr(package, "path", "")) for package in packages)  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]


# The refusals first: unlisted, the extension does nothing; a run with
# nothing to collect starts no pytest, and a step that nothing answers
# with a runner runs nothing and says why.


def test_unlisted_it_registers_no_step_requires_no_tool_and_writes_no_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.footman import _registry as footman_registry
    from livery.workshop._extensions import mount_extensions
    from livery.workshop._shipped_files import deliver
    from livery.workshop._steps import steps_by_name, tools_for

    root = tmp_path / "ws"
    root.mkdir()
    (root / "workshop.toml").write_text(CONTRACT + "extensions = []\n")
    monkeypatch.setattr(
        "livery.workshop._extensions.workspace_root", lambda start=None: root
    )
    state = registry.snapshot()
    try:
        with footman_registry.capture():
            assert mount_extensions(root) == ()
        assert "test.pytest" not in steps_by_name()
        assert "examples.pytest" not in steps_by_name()
        assert "pytest" not in {tool for tool, _ in tools_for(("python",))}
        assert "pytest>=9.0" not in _dev_group(root)
        deliver(root)
        assert not (root / "pytest.toml").exists()
        assert not (root / ".coveragerc").exists()
        # Listed, the mount registers both steps under the listed name,
        # the tool joins the profile, pytest rides the dev group, and the
        # sync writes both files.
        (root / "workshop.toml").write_text(CONTRACT + 'extensions = ["pytest"]\n')
        with footman_registry.capture():
            assert mount_extensions(root) == ("pytest",)
        assert steps_by_name()["test.pytest"].extension == "pytest"
        assert steps_by_name()["examples.pytest"].extension == "pytest"
        assert "pytest" in {tool for tool, _ in tools_for(("python",))}
        assert "pytest>=9.0" in _dev_group(root)
        deliver(root)
        settings = tomllib.loads((root / "pytest.toml").read_text())["pytest"]
        assert settings["cache_dir"] == ".workshop/.cache/pytest"
        # Each option is a word of its own: pytest reads the list as argv.
        assert settings["addopts"][:3] == ["-q", "-n", "auto"]
    finally:
        registry.restore(state)


#: The lines the dev group gains while pytest measures for coverage.
METER = {"pytest-cov>=5", "coverage>=7.13"}

#: The data coverage.py's meter leaves, which pytest's lines ignore.
METER_DATA = {".coverage", ".coverage.*"}


def _listed(root: Path, extensions: str) -> None:
    """List *extensions*, a TOML list as written, then mount and deliver at *root*."""
    from livery.footman import _registry as footman_registry
    from livery.workshop._extensions import mount_extensions
    from livery.workshop._shipped_files import deliver

    (root / "workshop.toml").write_text(CONTRACT + f"extensions = {extensions}\n")
    with footman_registry.capture():
        mount_extensions(root)
    deliver(root)


def test_without_coverage_no_coveragerc_is_written_and_the_dev_group_carries_no_meter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    monkeypatch.setattr(
        "livery.workshop._extensions.workspace_root", lambda start=None: root
    )
    # Listed alone, the suites run with no meter: nothing configures
    # one, installs one, or ignores the data one leaves.
    _listed(root, '["pytest"]')
    assert (root / "pytest.toml").is_file()
    assert not (root / ".coveragerc").exists()
    assert "pytest>=9.0" in _dev_group(root)
    assert not METER & set(_dev_group(root))
    assert not METER_DATA & set((root / ".gitignore").read_text().splitlines())
    # Listed beside coverage with its contribution taken off its entry,
    # pytest still measures nothing.
    _listed(root, '[{ name = "pytest", for = [] }, "coverage"]')
    assert not (root / ".coveragerc").exists()
    assert not METER & set(_dev_group(root))
    # Listed beside coverage, it measures for it: the configuration, the
    # meter in the dev group, and the lines that ignore the meter's data.
    _listed(root, '["pytest", "coverage"]')
    assert (root / ".coveragerc").is_file()
    assert set(_dev_group(root)) >= METER
    assert set((root / ".gitignore").read_text().splitlines()) >= METER_DATA
    # Taken off again, the sync withdraws the file it wrote.
    _listed(root, '["pytest"]')
    assert not (root / ".coveragerc").exists()


def test_the_delivered_coveragerc_names_each_file_from_the_workspace_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The legs' data and the union read a file by its name from the
    # workspace root, which coverage.py writes only under relative_files;
    # with absolute names the union finds no package's files.
    root = tmp_path / "ws"
    root.mkdir()
    monkeypatch.setattr(
        "livery.workshop._extensions.workspace_root", lambda start=None: root
    )
    _listed(root, '["pytest", "coverage"]')
    coverage = configparser.ConfigParser()
    coverage.read_string((root / ".coveragerc").read_text())
    assert coverage["run"].getboolean("relative_files")
    assert coverage["run"]["source"] == "acme"
    # A module a test wrote under its tmp_path and ran is never a
    # source: the directory is gone before the report is written.
    assert coverage["run"]["omit"] == "*/pytest-of-*/*"
    assert coverage["paths"]["packages"].split() == ["packages/", "packages\\"]


def test_a_coverage_listing_with_no_measurer_is_dormant_and_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.footman import _registry as footman_registry
    from livery.workshop import _doctor, _extensions, _points

    root = tmp_path / "ws"
    root.mkdir()
    monkeypatch.setattr(
        "livery.workshop._extensions.workspace_root", lambda start=None: root
    )
    monkeypatch.setattr("livery.workshop._doctor.workspace_root", lambda: root)
    plugged: list[str] = []
    monkeypatch.setattr("livery.footman.plugin", plugged.append)

    def joined() -> list[tuple[str, tuple[str, ...]]]:
        """What coverage joined to the gate point's jobs: each job and its tasks."""
        return [
            (item.job.name, tuple(entry.task for entry in item.entries))
            for item in _points.joined_jobs("gate")
            if item.extension == "coverage"
        ]

    # Listed with nothing that measures, coverage is dormant: the mount
    # adds no entry and no judge, its plugin mounts all the same, and
    # both the extensions' list and the doctor say what would wake it.
    (root / "workshop.toml").write_text(CONTRACT + 'extensions = ["coverage"]\n')
    waits = "nothing listed measures; cmake and pytest would"
    assert _extensions.dormancy(root) == {"coverage": waits}
    with footman_registry.capture():
        assert "coverage" in _extensions.mount_extensions(root)
    assert plugged == ["livery.extensions.coverage"]
    assert joined() == []
    assert f"    dormant: {waits}" in _extensions.describe_extensions(root)
    found = [finding.what for finding in _doctor.host()]
    assert f"extensions: coverage: dormant, {waits}" in found
    # pytest listed beside it measures, and coverage wakes: its entry
    # joins each check leg and its judge the gate job.
    (root / "workshop.toml").write_text(
        CONTRACT + 'extensions = ["pytest", "coverage"]\n'
    )
    assert _extensions.dormancy(root) == {}
    with footman_registry.capture():
        _extensions.mount_extensions(root)
    assert joined() == [("check", ("coverage.leg",)), ("gate", ("coverage.union",))]
    assert not [
        line for line in _extensions.describe_extensions(root) if "dormant" in line
    ]


def test_the_root_tests_directory_is_configured_only_while_it_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A newborn has no root tests/: naming it would fail every pytest
    # run. The fallback first.
    from livery.footman import _registry as footman_registry
    from livery.workshop._extensions import mount_extensions
    from livery.workshop._shipped_files import deliver

    root = tmp_path / "ws"
    root.mkdir()
    (root / "workshop.toml").write_text(CONTRACT + 'extensions = ["pytest"]\n')
    monkeypatch.setattr(
        "livery.workshop._extensions.workspace_root", lambda start=None: root
    )
    state = registry.snapshot()
    try:
        with footman_registry.capture():
            assert mount_extensions(root) == ("pytest",)
        deliver(root)
        settings = tomllib.loads((root / "pytest.toml").read_text())["pytest"]
        assert settings["testpaths"] == [] and settings["pythonpath"] == []
        (root / "tests").mkdir()
        deliver(root)
        settings = tomllib.loads((root / "pytest.toml").read_text())["pytest"]
        assert settings["testpaths"] == ["tests"]
        assert settings["pythonpath"] == ["tests"]
    finally:
        registry.restore(state)


def test_a_failed_test_keeps_its_tmp_path_and_a_passed_test_s_goes_at_teardown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Without the rendered policy every session's tmp_path directories
    # stay until a later session, in any checkout on the machine,
    # deletes the oldest tree whole in its controller after its last
    # test; a full suite's tree runs to a hundred thousand files, and
    # that delete took a minute. The failed test's directory is the
    # fallback for inspection, so it is pinned first.
    from livery.footman import _registry as footman_registry
    from livery.workshop._extensions import mount_extensions
    from livery.workshop._shipped_files import deliver

    root = tmp_path / "ws"
    (root / "tests").mkdir(parents=True)
    (root / "workshop.toml").write_text(CONTRACT + 'extensions = ["pytest"]\n')
    monkeypatch.setattr(
        "livery.workshop._extensions.workspace_root", lambda start=None: root
    )
    state = registry.snapshot()
    try:
        with footman_registry.capture():
            assert mount_extensions(root) == ("pytest",)
        deliver(root)
    finally:
        registry.restore(state)
    settings = tomllib.loads((root / "pytest.toml").read_text())["pytest"]
    assert settings["tmp_path_retention_policy"] == "failed"
    (root / "tests" / "test_dirs.py").write_text(
        "from pathlib import Path\n\n\n"
        "def test_red(tmp_path: Path) -> None:\n"
        "    (tmp_path / 'evidence').write_text('red')\n"
        "    raise AssertionError('kept for inspection')\n\n\n"
        "def test_green(tmp_path: Path) -> None:\n"
        "    (tmp_path / 'evidence').write_text('green')\n"
    )
    # A named base makes each test's directory a known path; -n 0 after
    # the rendered addopts keeps them out of a worker's subdirectory.
    base = tmp_path / "base"
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "-n",
            "0",
            f"--basetemp={base}",
            "tests",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 1, run.stdout + run.stderr
    assert "1 failed, 1 passed" in run.stdout, run.stdout
    assert (base / "test_red0" / "evidence").read_text() == "red"
    assert not (base / "test_green0").exists()


def test_the_test_step_asks_for_the_coverage_that_installs_its_own_hook() -> None:
    from livery.workshop._declaration import DeclaredValue
    from livery.workshop._extensions import declaration

    found = declaration("pytest")
    assert found is not None
    # The meter rides the dev group while pytest measures for coverage.
    answered = found.target_queries["coverage"]["python.dev-group"]
    assert isinstance(answered, DeclaredValue)
    contributed = [str(value) for value in cast("list[object]", answered.value)]
    # 7.13 is the first coverage that installs its own startup hook: an
    # older one would leave every process the tests start unmetered.
    assert "coverage>=7.13" in contributed
    assert not any(
        value.startswith("coverage-enable-subprocess") for value in contributed
    )


def test_with_no_suite_to_run_no_pytest_starts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    registered: None,
) -> None:
    calls = _watch(monkeypatch)
    registry.step_for("test.pytest").run(PhaseContext(root=tmp_path, packages=()))
    assert calls == []
    assert "test.pytest: no python package and no workspace tests" in (
        capsys.readouterr().out
    )


def test_a_root_listing_no_python_runs_no_suite_of_its_own_tests_and_says_why(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    registered: None,
) -> None:
    calls = _watch(monkeypatch)
    # The root lists pytest alone, and its own tests hold the root's set.
    _workspace(tmp_path)
    one = _member(tmp_path, "one")
    (tmp_path / "tests").mkdir()
    step = registry.step_for("test.pytest")
    step.run(PhaseContext(root=tmp_path, packages=(one,)))
    assert [_paths(call) for call in calls] == [("packages/one",)]
    assert (
        "  test.pytest: tests skips (the root's set holds no python; list it in"
        " [workspace] extensions to run them)\n"
    ) in capsys.readouterr().out
    # Once the root lists python, its own tests run beside the member's.
    _workspace(tmp_path, '["pytest", "python"]')
    step.run(PhaseContext(root=tmp_path, packages=(one,)))
    assert _paths(calls[-1]) == ("packages/one", "tests")


def test_the_test_step_runs_nothing_and_says_why_while_nothing_answers_its_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    registered: None,
) -> None:
    calls = _watch(monkeypatch)
    _workspace(tmp_path, '["pytest", "python"]')
    # A C++ library lists cmake, so no package holds python, and the
    # workspace's own tests, which hold the root's set, are the one
    # suite the step reaches.
    lib = _member(tmp_path, "lib", extensions=("cmake",))
    (tmp_path / "tests").mkdir()
    step = registry.step_for("test.pytest")

    def unanswered(root: Path, query: object) -> None:
        del root, query

    with monkeypatch.context() as patched:
        patched.setattr("livery.extensions.pytest._steps.answer", unanswered)
        step.run(PhaseContext(root=tmp_path, packages=(lib,)))
    assert calls == []
    out = capsys.readouterr().out
    assert "test.pytest: packages/lib skips (not a python package)" in out
    assert (
        "test.pytest: runs nothing; nothing answers python.test.runner, which the"
        " python extension answers while the root's set or a package's holds"
        " python"
    ) in out
    # The root's python answers, and the workspace's own tests run alone.
    step.run(PhaseContext(root=tmp_path, packages=(lib,)))
    assert [_paths(call) for call in calls] == [("tests",)]


def test_the_examples_step_without_a_runner_skips_by_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    registered: None,
) -> None:
    ran = _watch_examples(monkeypatch)
    # The python extension answers for pytest alone, and this workspace
    # lists nothing, so nothing answers though a package lists python.
    _workspace(tmp_path, "[]")
    one, two = _member(tmp_path, "one"), _member(tmp_path, "two")
    step = registry.step_for("examples.pytest")
    step.run(PhaseContext(root=tmp_path, packages=(one, two)))
    assert ran == []
    out = capsys.readouterr().out
    for path in ("packages/one", "packages/two"):
        assert f"examples: {path} skips (nothing answers python.examples.runner)" in (
            out
        )
    # Listed, pytest is answered, and each package's examples run.
    _workspace(tmp_path)
    step.run(PhaseContext(root=tmp_path, packages=(one, two)))
    assert [call[0] for call in ran] == ["packages/one", "packages/two"]


# The test step: one call of python's runner for the suites, the
# workspace's own tests among them, and a call of its own for a suite
# that is not worker-safe.


def test_pytest_hands_the_suites_to_python_s_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registered: None
) -> None:
    calls = _watch(monkeypatch)
    _workspace(tmp_path)
    one = _member(tmp_path, "one")
    tests = {"packages/one": ("packages/one/tests/test_x.py",)}
    ctx = PhaseContext(
        root=tmp_path,
        packages=(one,),
        subset=(one,),
        tests=tests,
        point="merge",
        arguments=("-k", "name"),
    )
    registry.step_for("test.pytest").run(ctx)
    assert calls == [
        (
            ("-k", "name"),
            {
                "packages": (one,),
                "root": tmp_path,
                "selection": tests,
                "point": "merge",
                "measured": False,
            },
        )
    ]


def test_the_suites_run_measured_while_pytest_measures_for_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registered: None
) -> None:
    calls = _watch(monkeypatch)
    one = _member(tmp_path, "one")
    ctx = PhaseContext(root=tmp_path, packages=(one,))
    # The fallbacks first: coverage unlisted, and listed with pytest's
    # contribution taken off its entry; then pytest measures for it.
    for extensions, measured in (
        ('["pytest"]', False),
        ('[{ name = "pytest", for = [] }, "coverage"]', False),
        ('["pytest", "coverage"]', True),
    ):
        (tmp_path / "workshop.toml").write_text(
            CONTRACT + f"extensions = {extensions}\n"
        )
        registry.step_for("test.pytest").run(ctx)
        assert calls[-1][1]["measured"] is measured, extensions


def test_a_whole_run_runs_every_suite_in_one_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registered: None
) -> None:
    calls = _watch(monkeypatch)
    _workspace(tmp_path, '["pytest", "python"]')
    one, two = _member(tmp_path, "one"), _member(tmp_path, "two")
    (tmp_path / "tests").mkdir()
    ctx = PhaseContext(root=tmp_path, packages=(one, two), point="nightly")
    registry.step_for("test.pytest").run(ctx)
    assert len(calls) == 1
    arguments, options = calls[0]
    assert arguments == ()
    assert _paths(calls[0]) == ("packages/one", "packages/two", "tests")
    assert options["point"] == "nightly"
    assert options["root"] == tmp_path


def test_a_suite_that_is_not_worker_safe_runs_alone_under_one_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registered: None
) -> None:
    calls = _watch(monkeypatch)
    _workspace(tmp_path)
    one, two = _member(tmp_path, "one"), _member(tmp_path, "two", serial=True)
    ctx = PhaseContext(root=tmp_path, packages=(one, two))
    registry.step_for("test.pytest").run(ctx)
    # Each call collects its own packages, so neither runs the other's.
    assert [(_paths(call), call[0]) for call in calls] == [
        (("packages/one",), ()),
        (("packages/two",), ("-n", "0")),
    ]
    # The words after -- on the step's own verb reach both calls, and
    # the serial call's -n 0 comes after them, so they cannot undo it.
    calls.clear()
    words = ("-k", "name", "-n", "4")
    ctx = PhaseContext(root=tmp_path, packages=(one, two), arguments=words)
    registry.step_for("test.pytest").run(ctx)
    assert [(_paths(call), call[0]) for call in calls] == [
        (("packages/one",), words),
        (("packages/two",), (*words, "-n", "0")),
    ]


def test_a_scoped_run_skips_a_suite_whose_examples_alone_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registered: None
) -> None:
    calls = _watch(monkeypatch)
    _workspace(tmp_path)
    one, two = _member(tmp_path, "one"), _member(tmp_path, "two")
    tests = {"packages/one": ("packages/one/tests/test_x.py",)}
    ctx = PhaseContext(
        root=tmp_path,
        packages=(one, two),
        subset=(one, two),
        tests=tests,
        examples=("packages/two",),
    )
    registry.step_for("test.pytest").run(ctx)
    assert [_paths(call) for call in calls] == [("packages/one",)]
    assert calls[0][1]["selection"] == tests


def test_a_run_over_named_files_runs_the_suites_holding_one_it_claims(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registered: None
) -> None:
    calls = _watch(monkeypatch)
    _workspace(tmp_path)
    one, two = _member(tmp_path, "one"), _member(tmp_path, "two")
    named = ("packages/one/src/acme/one.py", "packages/two/README.md")
    catalogue = {
        "packages/one": (("src/acme/one.py", "source"),),
        "packages/two": (("README.md", "prose"),),
    }
    ctx = PhaseContext(
        root=tmp_path,
        packages=(one, two),
        subset=(one, two),
        files=named,
        catalogue=catalogue,
    )
    registry.step_for("test.pytest").run(ctx)
    assert [_paths(call) for call in calls] == [("packages/one",)]


# The examples step: each member's examples, by python's runner.


def test_the_examples_run_by_python_s_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registered: None
) -> None:
    ran = _watch_examples(monkeypatch)
    _workspace(tmp_path)
    one = _member(tmp_path, "one")
    record = registry.step_for("examples.pytest")
    assert record.phase == "examples" and record.extensions == ("python",)
    assert [claim.category for claim in record.claims] == ["example"]
    record.run(PhaseContext(root=tmp_path, packages=(one,)))
    assert ran == [("packages/one", tmp_path, (), ())]
    # A scoped run whose change is the package's tests alone runs none;
    # one whose change is its examples alone runs them.
    ran.clear()
    tests = {"packages/one": ("packages/one/tests/test_x.py",)}
    record.run(PhaseContext(root=tmp_path, packages=(one,), subset=(one,), tests=tests))
    assert ran == []
    alone = ("packages/one",)
    record.run(
        PhaseContext(root=tmp_path, packages=(one,), subset=(one,), examples=alone)
    )
    assert ran == [("packages/one", tmp_path, (), ())]
    ran.clear()
    # A run over named files runs the named examples alone.
    example = "packages/one/docs/examples/first.py"
    ctx = PhaseContext(
        root=tmp_path,
        packages=(one,),
        subset=(one,),
        files=(example,),
        catalogue={"packages/one": (("docs/examples/first.py", "example"),)},
    )
    record.run(ctx)
    assert ran == [("packages/one", tmp_path, (str(tmp_path / example),), ())]
    # The words after -- on the step's own verb reach the runner.
    record.run(PhaseContext(root=tmp_path, packages=(one,), arguments=("-x",)))
    assert ran[-1][3] == ("-x",)
