"""The pytest extension: unlisted it does nothing; listed, it runs the suites.

Its steps run the suites and the examples through the functions the
python extension answers with, and run nothing, saying why, while
nothing answers. Beside coverage it measures them: the configuration,
the meter and the ignore lines arrive with coverage, and a coverage
listing nothing measures for is dormant and named.
"""

from __future__ import annotations

import configparser
import json
import subprocess
import sys
import tomllib
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from livery.extensions.pytest import _slice as pytest_slice
from livery.extensions.python import _backend as _python
from livery.workshop import (
    Package,
    PhaseContext,
    Query,
    RunContext,
    Shard,
    ShardMarker,
    answer,
    files_digest,
    partition,
    read_shard,
)
from livery.workshop import _steps as registry
from livery.workshop._shard import ONE

if TYPE_CHECKING:
    from livery.workshop._composition import Lookup

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
        assert settings["addopts"] == [*PYTEST_OPTIONS]
    finally:
        registry.restore(state)


#: The options the rendered pytest.toml hands every run, word by word,
#: before what another extension adds.
PYTEST_OPTIONS = ("-q", "-n", "auto", "--dist=worksteal", "--import-mode=importlib")


def test_another_extension_s_options_follow_pytest_s_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop import _queries

    root = tmp_path / "ws"
    root.mkdir()
    monkeypatch.setattr(
        "livery.workshop._extensions.workspace_root", lambda start=None: root
    )
    real = _queries.workspace_answers
    asked: list[object] = []

    def answered(root: Path, *, lookup: Lookup | None = None) -> dict[str, object]:
        found = real(root, lookup=lookup)
        # pytest declares the query, and with nothing else listed nobody
        # answers it.
        asked.append(found["python.test.addopts"])
        return {**found, "python.test.addopts": ["-p no:randomly", "--strict-markers"]}

    monkeypatch.setattr(_queries, "workspace_answers", answered)
    _listed(root, '["pytest"]')
    assert asked and all(found == () for found in asked)
    settings = tomllib.loads((root / "pytest.toml").read_text())["pytest"]
    assert settings["addopts"] == [
        *PYTEST_OPTIONS,
        "-p",
        "no:randomly",
        "--strict-markers",
    ]


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
    _listed(root, '[{ name = "pytest", without = ["coverage"] }, "coverage"]')
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


def test_coverage_leaving_docs_out_adds_no_docs_step_and_pytest_still_measures_for_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._extensions import closure_problems, contributing, dormancy
    from livery.workshop._steps import steps_by_name

    root = tmp_path / "ws"
    root.mkdir()
    monkeypatch.setattr(
        "livery.workshop._extensions.workspace_root", lambda start=None: root
    )
    coverage_steps = {"docs.coverage", "deploy.coverage"}
    state = registry.snapshot()
    try:
        # Coverage's entry leaves docs out: its [for.docs] steps are absent
        # from both site phases, and pytest's [for.coverage] table, which
        # no entry leaves out, still applies.
        _listed(root, '[{ name = "coverage", without = ["docs"] }, "docs", "pytest"]')
        assert closure_problems(root) == []
        assert not coverage_steps & set(steps_by_name())
        assert contributing(root) == {"pytest": ("coverage",)}
        assert dormancy(root) == {}
        assert (root / ".coveragerc").is_file()
        assert set(_dev_group(root)) >= METER
        # Listed by name, coverage contributes its docs steps too.
        _listed(root, '["coverage", "docs", "pytest"]')
        assert closure_problems(root) == []
        assert coverage_steps <= set(steps_by_name())
        assert contributing(root) == {"coverage": ("docs",), "pytest": ("coverage",)}
    finally:
        registry.restore(state)


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
        ('[{ name = "pytest", without = ["coverage"] }, "coverage"]', False),
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


# The sharded check leg: the test step runs its shard's share of the
# test files. The fallbacks first: a store that cannot be read and a
# leg with no durations row leave every file to the first shard; a
# suite with no file in the share is left out of the call. Then the
# partition, the serial suite, one shard of one, and the top-up.

#: The bound every shard of a run reads the durations under: the
#: checkout's committer time, which the stand-in answers.
BOUND = datetime(2026, 10, 17, 9, 30, tzinfo=UTC)

#: The platform leg whose durations a shard reads.
LEG = "check-ubuntu-latest-3.14"


def _sharded(
    monkeypatch: pytest.MonkeyPatch,
    *,
    durations: dict[str, float] | None = None,
    rows: tuple[str, ...] = (),
    reason: str = "",
    leg: str = f"{LEG}-1of2",
    ci: bool = True,
) -> list[tuple[str, datetime]]:
    """Stand in for the store and the checkout; each read the step makes of the store.

    The durations series answers *durations* for the rows *rows*, or
    *reason* when it cannot be read, and the checkout's committer
    time is `BOUND`. The run is a CI run on *leg*, or none outside CI.
    """
    asked: list[tuple[str, datetime]] = []

    def read_durations(
        root: Path, leg: str, *, before: datetime
    ) -> tuple[dict[str, float], tuple[str, ...], str]:
        asked.append((leg, before))
        return dict(durations or {}), tuple(rows), reason

    monkeypatch.setattr(pytest_slice, "read_durations", read_durations)
    monkeypatch.setattr(pytest_slice, "checkout_time", lambda root: BOUND)
    run = RunContext("gitea", "7", "push", "refs/heads/main", leg=leg) if ci else None
    monkeypatch.setattr(pytest_slice, "ci_run", lambda: run)
    return asked


def _tests_of(package: Package, *names: str) -> tuple[str, ...]:
    """Write empty test files *names* under *package*'s tests, named from the root."""
    written: list[str] = []
    for name in names:
        path = package.directory / "tests" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
        written.append(f"{package.path}/tests/{name}")
    return tuple(written)


def _selection(call: Call) -> dict[str, tuple[str, ...]]:
    selection = call[1]["selection"]
    assert isinstance(selection, dict)
    return cast("dict[str, tuple[str, ...]]", selection)


def test_the_test_step_reads_the_shard_flag(registered: None) -> None:
    assert registry.step_for("test.pytest").flags == ("point", "shard")


def test_an_unreadable_durations_store_leaves_every_test_file_to_the_first_shard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    registered: None,
) -> None:
    calls = _watch(monkeypatch)
    _workspace(tmp_path)
    one, two = _member(tmp_path, "one"), _member(tmp_path, "two")
    files = _tests_of(one, "test_a.py", "test_b.py") + _tests_of(two, "test_c.py")
    # The files beside the tests are support, never a test file.
    (two.directory / "tests" / "conftest.py").write_text("")
    (two.directory / "tests" / "helper.py").write_text("")
    down = f"the durations/{LEG} series could not be read: down"
    asked = _sharded(monkeypatch, reason=down, leg=f"{LEG}-2of2")
    step = registry.step_for("test.pytest")
    step.run(PhaseContext(root=tmp_path, packages=(one, two), shard=Shard(2, 2)))
    out = capsys.readouterr().out
    # The second shard runs nothing and says why; its record names no
    # row, so the gate job's proof sees the leg ran unsharded.
    assert calls == []
    assert (
        f"  test.pytest: the durations of {LEG} could not be read ({down}); the"
        " first shard runs every test file\n"
    ) in out
    assert "  test.pytest: shard 2of2 runs none of 3 test file(s)\n" in out
    assert asked == [(f"{LEG}-2of2", BOUND)]
    left, why = read_shard(tmp_path)
    assert why == "" and left is not None
    assert left.shard == Shard(2, 2) and left.files == () and left.rows == ()
    assert left.digest == files_digest(files) and left.before == BOUND
    # The first shard runs every file in one call, and records them.
    _sharded(monkeypatch, reason=down, leg=f"{LEG}-1of2")
    step.run(PhaseContext(root=tmp_path, packages=(one, two), shard=Shard(1, 2)))
    assert "  test.pytest: shard 1of2 runs 3 of 3 test file(s)\n" in (
        capsys.readouterr().out
    )
    assert len(calls) == 1
    assert _paths(calls[0]) == ("packages/one", "packages/two")
    assert _selection(calls[0]) == {
        "packages/one": files[:2],
        "packages/two": files[2:],
    }
    left, why = read_shard(tmp_path)
    assert why == "" and left is not None
    assert left.shard == Shard(1, 2) and left.files == files and left.rows == ()
    assert left.digest == files_digest(files)


def test_with_no_durations_row_before_the_bound_the_first_shard_runs_every_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    registered: None,
) -> None:
    calls = _watch(monkeypatch)
    _workspace(tmp_path)
    one = _member(tmp_path, "one")
    files = _tests_of(one, "test_a.py", "test_b.py")
    step = registry.step_for("test.pytest")
    # A readable series with no row before the bound is the first run
    # of a sharded leg: the first shard runs the whole suite.
    _sharded(monkeypatch, durations={}, rows=(), leg=f"{LEG}-2of2")
    step.run(PhaseContext(root=tmp_path, packages=(one,), shard=Shard(2, 2)))
    out = capsys.readouterr().out
    assert calls == []
    assert (
        f"  test.pytest: no durations row of {LEG} before"
        " 2026-10-17T09:30:00+00:00; the first shard runs every test file\n"
    ) in out
    _sharded(monkeypatch, durations={}, rows=(), leg=f"{LEG}-1of2")
    step.run(PhaseContext(root=tmp_path, packages=(one,), shard=Shard(1, 2)))
    assert [_selection(call) for call in calls] == [{"packages/one": files}]
    left, why = read_shard(tmp_path)
    assert why == "" and left is not None
    assert left.files == files and left.rows == ()


def test_a_suite_with_no_file_in_the_share_is_left_out_of_the_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    registered: None,
) -> None:
    calls = _watch(monkeypatch)
    _workspace(tmp_path)
    one, two = _member(tmp_path, "one"), _member(tmp_path, "two")
    (a,) = _tests_of(one, "test_a.py")
    c, d = _tests_of(two, "test_c.py", "test_d.py")
    # A member without a test directory has no file to share out.
    three = _member(tmp_path, "three")
    (three.directory / "tests").rmdir()
    packages = (one, two, three)
    # one's file outweighs two's together, so the first shard is one's
    # alone and the second two's alone.
    rows = ("r2.json", "r1.json")
    weights = {a: 100.0, c: 1.0, d: 1.0}
    _sharded(monkeypatch, durations=weights, rows=rows, leg=f"{LEG}-2of2")
    step = registry.step_for("test.pytest")
    step.run(PhaseContext(root=tmp_path, packages=packages, shard=Shard(2, 2)))
    assert [(_paths(call), _selection(call)) for call in calls] == [
        (("packages/two",), {"packages/two": (c, d)})
    ]
    assert "  test.pytest: shard 2of2 runs 2 of 3 test file(s)\n" in (
        capsys.readouterr().out
    )
    # The record names the suites with a file in the set; the member
    # without one is left to the first shard whole, as a step that does
    # not read the shard is.
    left, why = read_shard(tmp_path)
    assert why == "" and left is not None
    assert left.suites == ("packages/one", "packages/two")
    calls.clear()
    _sharded(monkeypatch, durations=weights, rows=rows, leg=f"{LEG}-1of2")
    step.run(PhaseContext(root=tmp_path, packages=packages, shard=Shard(1, 2)))
    assert [(_paths(call), _selection(call)) for call in calls] == [
        (("packages/one",), {"packages/one": (a,)})
    ]
    # More shards than files: the surplus shard runs nothing and says so.
    calls.clear()
    _sharded(monkeypatch, durations=weights, rows=rows, leg=f"{LEG}-4of4")
    step.run(PhaseContext(root=tmp_path, packages=packages, shard=Shard(4, 4)))
    assert calls == []
    assert "  test.pytest: shard 4of4 runs none of 3 test file(s)\n" in (
        capsys.readouterr().out
    )


def test_two_shards_hand_the_runner_complementary_shares_of_every_test_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registered: None
) -> None:
    calls = _watch(monkeypatch)
    _workspace(tmp_path, '["pytest", "python"]')
    one, two = _member(tmp_path, "one"), _member(tmp_path, "two")
    # Nested files and the *_test.py spelling are test files too; the
    # workspace's own tests are a suite of their own.
    a, b, n, x = _tests_of(one, "test_a.py", "test_b.py", "sub/test_n.py", "x_test.py")
    (c,) = _tests_of(two, "test_c.py")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_w.py").write_text("")
    w = "tests/test_w.py"
    every = (a, b, n, x, c, w)
    durations = {a: 5.0, b: 3.0, x: 2.0, c: 4.0, w: 1.0}  # n is unseen
    rows = ("r2.json", "r1.json")
    step = registry.step_for("test.pytest")
    records: list[ShardMarker] = []
    for index in (1, 2):
        _sharded(monkeypatch, durations=durations, rows=rows, leg=f"{LEG}-{index}of2")
        step.run(
            PhaseContext(root=tmp_path, packages=(one, two), shard=Shard(index, 2))
        )
        left, why = read_shard(tmp_path)
        assert why == "" and left is not None
        records.append(left)
    assert len(calls) == 2
    shares = [
        tuple(name for names in _selection(call).values() for name in names)
        for call in calls
    ]
    # Together the shares are every test file, and nothing is in both.
    assert sorted(shares[0] + shares[1]) == sorted(every)
    assert not set(shares[0]) & set(shares[1])
    # Each share is the partition's slice: a has the first shard to
    # itself until the rest outweigh it.
    expected = partition(every, durations, 2)
    assert [sorted(share) for share in shares] == [sorted(s) for s in expected]
    for index, (call, left) in enumerate(zip(calls, records, strict=True)):
        # Each suite's files are sorted, and a suite with no file in
        # the share is not in the call.
        selection = _selection(call)
        assert all(
            files == tuple(sorted(files)) and files for files in selection.values()
        )
        assert _paths(call) == tuple(selection)
        # The record: the slice in packing order, the digest of the
        # whole set, the rows read and the bound.
        assert left.shard == Shard(index + 1, 2)
        assert left.files == expected[index]
        assert left.digest == files_digest(every)
        assert left.rows == rows and left.before == BOUND
        assert left.suites == ("packages/one", "packages/two", "tests")
    assert records[0].digest == records[1].digest


def test_a_serial_suite_is_sliced_and_still_runs_under_one_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registered: None
) -> None:
    calls = _watch(monkeypatch)
    _workspace(tmp_path)
    one, two = _member(tmp_path, "one"), _member(tmp_path, "two", serial=True)
    a, b = _tests_of(one, "test_a.py", "test_b.py")
    c, d = _tests_of(two, "test_c.py", "test_d.py")
    # Equal weights: a and c land on the first shard, b and d on the second.
    _sharded(monkeypatch, durations=dict.fromkeys((a, b, c, d), 4.0), rows=("r1.json",))
    words = ("-k", "name")
    ctx = PhaseContext(
        root=tmp_path, packages=(one, two), shard=Shard(1, 2), arguments=words
    )
    registry.step_for("test.pytest").run(ctx)
    assert [(_paths(call), call[0], _selection(call)) for call in calls] == [
        (("packages/one",), words, {"packages/one": (a,)}),
        (("packages/two",), (*words, "-n", "0"), {"packages/two": (c,)}),
    ]


def test_one_shard_of_one_hands_the_runner_the_suites_whole_and_reads_no_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registered: None
) -> None:
    calls = _watch(monkeypatch)
    _workspace(tmp_path)
    one = _member(tmp_path, "one")
    a, b = _tests_of(one, "test_a.py", "test_b.py")
    asked = _sharded(monkeypatch, durations={a: 1.0, b: 9.0}, rows=("r1.json",))
    tests = {"packages/one": (a,)}
    contexts = (
        PhaseContext(root=tmp_path, packages=(one,), subset=(one,), tests=tests),
        PhaseContext(
            root=tmp_path, packages=(one,), subset=(one,), tests=tests, shard=ONE
        ),
    )
    for ctx in contexts:
        registry.step_for("test.pytest").run(ctx)
        # The run's own selection, the same object, and no record left.
        assert calls[-1][1]["selection"] is tests
        assert _paths(calls[-1]) == ("packages/one",)
    assert asked == []
    assert read_shard(tmp_path) == (None, "")
    assert not (tmp_path / pytest_slice.SETS_FILE).exists()


def test_without_a_leg_the_share_is_by_name_alone_and_no_store_is_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    registered: None,
) -> None:
    calls = _watch(monkeypatch)
    _workspace(tmp_path)
    one = _member(tmp_path, "one")
    a, b, c = _tests_of(one, "test_a.py", "test_b.py", "test_c.py")
    step = registry.step_for("test.pytest")
    # Outside CI, and in a CI process with no leg label: every file
    # weighs the same, so the files alternate by name.
    for ci, leg in ((False, ""), (True, "")):
        calls.clear()
        asked = _sharded(
            monkeypatch, durations={a: 100.0}, rows=("r1.json",), ci=ci, leg=leg
        )
        step.run(PhaseContext(root=tmp_path, packages=(one,), shard=Shard(2, 2)))
        out = capsys.readouterr().out
        assert asked == []
        assert [_selection(call) for call in calls] == [{"packages/one": (b,)}]
        assert (
            "  test.pytest: no leg names a durations series here; every test file"
            " weighs the same\n"
        ) in out
        assert "  test.pytest: shard 2of2 runs 1 of 3 test file(s)\n" in out
        left, why = read_shard(tmp_path)
        assert why == "" and left is not None
        assert left.files == (b,) and left.rows == () and left.before == BOUND
        assert left.digest == files_digest((a, b, c))


def test_the_share_takes_the_run_s_selection_when_it_names_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registered: None
) -> None:
    calls = _watch(monkeypatch)
    _workspace(tmp_path)
    one = _member(tmp_path, "one")
    a, b = _tests_of(one, "test_a.py", "test_b.py")
    tests = {"packages/one": (a,)}
    step = registry.step_for("test.pytest")
    # The changed test file alone stands for the suite: b is not
    # partitioned, so the second shard has nothing of the suite.
    _sharded(
        monkeypatch, durations={a: 1.0, b: 1.0}, rows=("r1.json",), leg=f"{LEG}-2of2"
    )
    step.run(
        PhaseContext(
            root=tmp_path,
            packages=(one,),
            subset=(one,),
            tests=tests,
            shard=Shard(2, 2),
        )
    )
    assert calls == []
    left, why = read_shard(tmp_path)
    assert why == "" and left is not None
    assert left.files == () and left.digest == files_digest((a,))
    assert left.suites == ("packages/one",)
    _sharded(
        monkeypatch, durations={a: 1.0, b: 1.0}, rows=("r1.json",), leg=f"{LEG}-1of2"
    )
    step.run(
        PhaseContext(
            root=tmp_path,
            packages=(one,),
            subset=(one,),
            tests=tests,
            shard=Shard(1, 2),
        )
    )
    assert [_selection(call) for call in calls] == [{"packages/one": (a,)}]


def test_a_second_run_of_the_step_on_the_shard_adds_to_its_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registered: None
) -> None:
    _watch(monkeypatch)
    _workspace(tmp_path)
    one, two = _member(tmp_path, "one"), _member(tmp_path, "two")
    a, b = _tests_of(one, "test_a.py", "test_b.py")
    c, d = _tests_of(two, "test_c.py", "test_d.py")
    durations = dict.fromkeys((a, b, c, d), 4.0)
    rows = ("r1.json",)
    step = registry.step_for("test.pytest")
    sets = tmp_path / pytest_slice.SETS_FILE

    def run(subset: tuple[Package, ...]) -> None:
        step.run(
            PhaseContext(
                root=tmp_path, packages=(one, two), subset=subset, shard=Shard(2, 2)
            )
        )

    def left() -> tuple[tuple[str, ...], str, tuple[str, ...]]:
        record, why = read_shard(tmp_path)
        assert why == "" and record is not None
        return record.files, record.digest, record.suites

    # A record of another shard, another bound or other rows, or one
    # that does not read, is replaced, never added to.
    _sharded(monkeypatch, durations=durations, rows=rows, leg=f"{LEG}-2of2")
    sets.parent.mkdir(parents=True)
    key = {"shard": "2of2", "before": BOUND.isoformat(), "rows": list(rows)}
    for stale in (
        "{not json",
        json.dumps([1, 2]),
        json.dumps({**key, "shard": "1of2", "set": [a], "files": [a]}),
        json.dumps({**key, "rows": ["r9.json"], "set": [a], "files": [a]}),
        json.dumps({**key, "set": "a", "files": [a], "suites": ["packages/one"]}),
        json.dumps({**key, "set": [a], "files": [1], "suites": ["packages/one"]}),
        json.dumps({**key, "set": [a], "files": [a], "suites": "packages/one"}),
        json.dumps({**key, "set": [a], "files": [a]}),
    ):
        sets.write_text(stale)
        run((one,))
        assert left() == ((b,), files_digest((a, b)), ("packages/one",))
    # The gate's run over one, then the top-up's run over two on the
    # same shard: the record holds both slices and the digest of both
    # sets, as one partition of one set.
    run((one,))
    assert left() == ((b,), files_digest((a, b)), ("packages/one",))
    both = ("packages/one", "packages/two")
    run((two,))
    assert left() == ((b, d), files_digest((a, b, c, d)), both)
    # Running it again adds nothing.
    run((two,))
    assert left() == ((b, d), files_digest((a, b, c, d)), both)
    # The first shard's record is its own.
    _sharded(monkeypatch, durations=durations, rows=rows, leg=f"{LEG}-1of2")
    step.run(
        PhaseContext(
            root=tmp_path, packages=(one, two), subset=(one,), shard=Shard(1, 2)
        )
    )
    assert left() == ((a,), files_digest((a, b)), ("packages/one",))


def test_every_run_of_the_step_takes_its_files_from_one_partition_of_the_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registered: None
) -> None:
    # The gate runs one member and the workspace's own tests; the top-up
    # then runs another member and the workspace's tests again. Packed
    # per run, the second set would split the workspace's tests apart
    # from the first, and two shards would run one file. Packed once
    # over every test file the workspace holds, a file has one shard
    # whichever run of the step reaches it.
    _watch(monkeypatch)
    _workspace(tmp_path, '["pytest", "python"]')
    one, two = _member(tmp_path, "one"), _member(tmp_path, "two")
    a, b = _tests_of(one, "test_a.py", "test_b.py")
    (c,) = _tests_of(two, "test_c.py")
    (tmp_path / "tests").mkdir()
    for name in ("test_w1.py", "test_w2.py"):
        (tmp_path / "tests" / name).write_text("")
    w1, w2 = "tests/test_w1.py", "tests/test_w2.py"
    durations = {a: 10.0, b: 1.0, c: 1.0, w1: 5.0, w2: 5.0}
    whole = partition((a, b, c, w1, w2), durations, 2)
    step = registry.step_for("test.pytest")
    ran: list[set[str]] = []
    digests: list[str] = []
    for index in (1, 2):
        _sharded(
            monkeypatch, durations=durations, rows=("r1.json",), leg=f"{LEG}-{index}of2"
        )
        (tmp_path / pytest_slice.SETS_FILE).unlink(missing_ok=True)
        for subset in ((one,), (two,)):
            step.run(
                PhaseContext(
                    root=tmp_path,
                    packages=(one, two),
                    subset=subset,
                    shard=Shard(index, 2),
                )
            )
        record, why = read_shard(tmp_path)
        assert why == "" and record is not None
        ran.append(set(record.files))
        digests.append(record.digest)
        # Each shard runs its own slice of the one partition, and no more.
        assert set(record.files) <= set(whole[index - 1])
    assert not ran[0] & ran[1]
    assert ran[0] | ran[1] == {a, b, c, w1, w2}
    assert digests[0] == digests[1]


def test_the_top_up_runs_through_the_step_and_is_sliced_by_the_same_partition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    registered: None,
) -> None:
    from livery.extensions.coverage import _leg as coverage_leg
    from livery.extensions.coverage import _record as coverage_record
    from livery.workshop import LegScope

    calls = _watch(monkeypatch)
    _workspace(tmp_path)
    one, two = _member(tmp_path, "one"), _member(tmp_path, "two")
    a, b = _tests_of(one, "test_a.py", "test_b.py")
    c, d = _tests_of(two, "test_c.py", "test_d.py")
    durations = dict.fromkeys((a, b, c, d), 4.0)
    leg = f"{LEG}-2of2"
    run = RunContext("gitea", "7", "push", "refs/heads/main", leg=leg)
    _sharded(monkeypatch, durations=durations, rows=("r1.json",), leg=leg)
    monkeypatch.setattr("livery.workshop._quality.workspace_root", lambda: tmp_path)
    monkeypatch.setattr("livery.workshop._quality._packages", lambda: (one, two))
    monkeypatch.setattr(
        "livery.workshop._ci_context.run_context", lambda environ=None: run
    )
    monkeypatch.setattr(coverage_record, "closure_id", lambda git, ps, p: "k" * 64)
    monkeypatch.setattr(
        coverage_record,
        "recorded",
        lambda root, *, leg, base="main": coverage_record.Record({}),
    )
    # The gate's narrowed run over one, on the second shard.
    step = registry.step_for("test.pytest")
    step.run(
        PhaseContext(
            root=tmp_path, packages=(one, two), subset=(one,), shard=Shard(2, 2)
        )
    )
    assert [_selection(call) for call in calls] == [{"packages/one": (b,)}]
    # The coverage leg's top-up runs two, which no record supplies,
    # through the test phase: the step reads the leg's shard, hands the
    # runner two's share alone, and adds it to the shard's record.
    marker = LegScope("affected", ("packages/one",), leg, "b" * 40)
    measured = coverage_leg.top_up(tmp_path, (one, two), marker)
    out = capsys.readouterr().out
    assert "  measuring: packages/two run for their lines alone\n" in out
    assert [_selection(call) for call in calls] == [
        {"packages/one": (b,)},
        {"packages/two": (d,)},
    ]
    assert measured.packages == ("packages/one", "packages/two")
    left, why = read_shard(tmp_path)
    assert why == "" and left is not None
    assert left.files == (b, d) and left.digest == files_digest((a, b, c, d))
    # The leg then leaves parts for both suites, each with a file in the share.
    assert [
        unit.path
        for unit in coverage_leg.suites_that_ran(measured, tmp_path, (one, two))
    ] == [
        "packages/one",
        "packages/two",
    ]
