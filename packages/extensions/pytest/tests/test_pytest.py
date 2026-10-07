"""The pytest extension: unlisted it does nothing; listed, it runs the suites."""

from __future__ import annotations

import configparser
import tomllib
from collections.abc import Iterator
from pathlib import Path

import pytest

from livery.extensions.pytest import _checks
from livery.workshop import GateContext, Package
from livery.workshop import _checks as registry

CONTRACT = (
    "[workspace]\n"
    'name = "acme"\n'
    'namespace = "acme"\n'
    'authors = [{ name = "Acme", email = "dev@acme.test" }]\n'
    'copyright-year = "2026"\n'
)


@pytest.fixture
def registered() -> Iterator[None]:
    """Pytest's checks registered, as the mount registers a listed extension's."""
    from livery.workshop._extensions import declaration, register_declared

    state = registry.snapshot()
    found = declaration("pytest")
    assert found is not None
    register_declared("pytest", found.additions)
    try:
        yield
    finally:
        registry.restore(state)


def _member(root: Path, name: str, *, serial: bool = False) -> Package:
    """A python member; *serial* sets its suite's ``parallel`` option off."""
    directory = root / "packages" / name
    (directory / "tests").mkdir(parents=True)
    (directory / "docs" / "examples").mkdir(parents=True)
    checks: tuple[tuple[str, tuple[tuple[str, object], ...]], ...] = ()
    if serial:
        checks = (("checks.pytest", (("parallel", False),)),)
    return Package(
        directory=directory,
        path=f"packages/{name}",
        name=f"acme-{name}",
        kind="python",
        depends=(),
        checks=checks,
    )


def _dev_group(slots: dict[str, object]) -> list[str]:
    """The dev group's lines, as the slots compose them."""
    group = slots["python.dev-group"]
    assert isinstance(group, list)
    return [str(line) for line in group]  # pyright: ignore[reportUnknownVariableType]


Call = tuple[tuple[str, ...], dict[str, object]]


def _watch(monkeypatch: pytest.MonkeyPatch) -> list[Call]:
    """Each call the test check makes of the kind's suite runner."""
    calls: list[Call] = []

    def run_suites(kind: str, *arguments: str, **options: object) -> None:
        assert kind == "python"
        calls.append((arguments, options))

    monkeypatch.setattr(_checks, "run_suites", run_suites)
    return calls


def _paths(call: Call) -> tuple[str, ...]:
    packages = call[1]["packages"]
    assert isinstance(packages, tuple)
    return tuple(str(getattr(package, "path", "")) for package in packages)  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]


# The refusals first: unlisted, the extension does nothing; a run with
# nothing to collect starts no pytest.


def test_unlisted_it_registers_no_check_requires_no_tool_and_writes_no_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.footman import _registry as footman_registry
    from livery.workshop._checks import checks_by_name, tools_for_kind
    from livery.workshop._extensions import mount_extensions
    from livery.workshop._shipped_files import deliver
    from livery.workshop._slots import all_composed

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
        assert "test.pytest" not in checks_by_name()
        assert "examples.pytest" not in checks_by_name()
        assert "pytest" not in {tool for tool, _ in tools_for_kind("python")}
        assert "pytest>=9.0" not in _dev_group(all_composed())
        deliver(root)
        assert not (root / "pytest.toml").exists()
        assert not (root / ".coveragerc").exists()
        # Listed, the mount registers both checks under the listed name,
        # the tool joins the profile, pytest rides the dev group, and the
        # sync writes both files.
        (root / "workshop.toml").write_text(CONTRACT + 'extensions = ["pytest"]\n')
        with footman_registry.capture():
            assert mount_extensions(root) == ("pytest",)
        assert checks_by_name()["test.pytest"].extension == "pytest"
        assert checks_by_name()["examples.pytest"].extension == "pytest"
        assert "pytest" in {tool for tool, _ in tools_for_kind("python")}
        assert "pytest>=9.0" in _dev_group(all_composed())
        deliver(root)
        settings = tomllib.loads((root / "pytest.toml").read_text())["pytest"]
        assert settings["cache_dir"] == ".workshop/.cache/pytest"
        # Each option is a word of its own: pytest reads the list as argv.
        assert settings["addopts"][:3] == ["-q", "-n", "auto"]
        coverage = configparser.ConfigParser()
        coverage.read_string((root / ".coveragerc").read_text())
        assert coverage["run"]["source"] == "acme"
        assert coverage["paths"]["packages"].split() == ["packages/", "packages\\"]
    finally:
        registry.restore(state)


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


def test_the_test_check_asks_for_the_coverage_that_installs_its_own_hook() -> None:
    from livery.workshop._extensions import declaration

    found = declaration("pytest")
    assert found is not None
    contributed = [
        value
        for slot, value in found.additions.contributions
        if slot == "python.dev-group"
    ]
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
    registry.check_for("test.pytest").run(GateContext(root=tmp_path, packages=()))
    assert calls == []
    assert "test.pytest: no python package and no workspace tests" in (
        capsys.readouterr().out
    )


# The test check: one call for the suites, the workspace's own tests
# among them, and a call of its own for a suite that is not worker-safe.


def test_a_whole_run_runs_every_suite_in_one_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registered: None
) -> None:
    calls = _watch(monkeypatch)
    one, two = _member(tmp_path, "one"), _member(tmp_path, "two")
    (tmp_path / "tests").mkdir()
    ctx = GateContext(root=tmp_path, packages=(one, two), point="nightly")
    registry.check_for("test.pytest").run(ctx)
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
    one, two = _member(tmp_path, "one"), _member(tmp_path, "two", serial=True)
    ctx = GateContext(root=tmp_path, packages=(one, two))
    registry.check_for("test.pytest").run(ctx)
    # Each call collects its own packages, so neither runs the other's.
    assert [(_paths(call), call[0]) for call in calls] == [
        (("packages/one",), ()),
        (("packages/two",), ("-n", "0")),
    ]
    # The words after -- on the check's own verb reach both calls, and
    # the serial call's -n 0 comes after them, so they cannot undo it.
    calls.clear()
    words = ("-k", "name", "-n", "4")
    ctx = GateContext(root=tmp_path, packages=(one, two), arguments=words)
    registry.check_for("test.pytest").run(ctx)
    assert [(_paths(call), call[0]) for call in calls] == [
        (("packages/one",), words),
        (("packages/two",), (*words, "-n", "0")),
    ]


def test_a_scoped_run_skips_a_suite_whose_examples_alone_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registered: None
) -> None:
    calls = _watch(monkeypatch)
    one, two = _member(tmp_path, "one"), _member(tmp_path, "two")
    tests = {"packages/one": ("packages/one/tests/test_x.py",)}
    ctx = GateContext(
        root=tmp_path,
        packages=(one, two),
        subset=(one, two),
        tests=tests,
        examples=("packages/two",),
    )
    registry.check_for("test.pytest").run(ctx)
    assert [_paths(call) for call in calls] == [("packages/one",)]
    assert calls[0][1]["selection"] == tests


def test_a_run_over_named_files_runs_the_suites_holding_one_it_claims(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registered: None
) -> None:
    calls = _watch(monkeypatch)
    one, two = _member(tmp_path, "one"), _member(tmp_path, "two")
    named = ("packages/one/src/acme/one.py", "packages/two/README.md")
    catalogue = {
        "packages/one": (("src/acme/one.py", "source"),),
        "packages/two": (("README.md", "prose"),),
    }
    ctx = GateContext(
        root=tmp_path,
        packages=(one, two),
        subset=(one, two),
        files=named,
        catalogue=catalogue,
    )
    registry.check_for("test.pytest").run(ctx)
    assert [_paths(call) for call in calls] == [("packages/one",)]


# The examples check: each member's examples, by its kind's runner.


def test_the_examples_run_by_the_kind_s_runner_and_a_kind_with_none_skips(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    registered: None,
) -> None:
    ran: list[tuple[str, tuple[str, ...]]] = []
    handed: list[tuple[str, ...]] = []

    def runner(
        package: Package, root: Path, files: tuple[str, ...], arguments: tuple[str, ...]
    ) -> None:
        assert root == tmp_path
        ran.append((package.path, files))
        handed.append(arguments)

    monkeypatch.setattr(_checks, "kind_examples", lambda kind: runner)
    one = _member(tmp_path, "one")
    record = registry.check_for("examples.pytest")
    assert record.role == "examples" and record.kinds == ("python",)
    assert [claim.category for claim in record.claims] == ["example"]
    record.run(GateContext(root=tmp_path, packages=(one,)))
    assert ran == [("packages/one", ())]
    # A scoped run whose change is the package's tests alone runs none;
    # one whose change is its examples alone runs them.
    ran.clear()
    tests = {"packages/one": ("packages/one/tests/test_x.py",)}
    record.run(GateContext(root=tmp_path, packages=(one,), subset=(one,), tests=tests))
    assert ran == []
    alone = ("packages/one",)
    record.run(
        GateContext(root=tmp_path, packages=(one,), subset=(one,), examples=alone)
    )
    assert ran == [("packages/one", ())]
    ran.clear()
    # A run over named files runs the named examples alone.
    example = "packages/one/docs/examples/first.py"
    ctx = GateContext(
        root=tmp_path,
        packages=(one,),
        subset=(one,),
        files=(example,),
        catalogue={"packages/one": (("docs/examples/first.py", "example"),)},
    )
    record.run(ctx)
    assert ran == [("packages/one", (str(tmp_path / example),))]
    # The words after -- on the check's own verb reach the runner.
    assert set(handed) == {()}
    record.run(GateContext(root=tmp_path, packages=(one,), arguments=("-x",)))
    assert handed[-1] == ("-x",)
    monkeypatch.setattr(_checks, "kind_examples", lambda kind: None)

    record.run(GateContext(root=tmp_path, packages=(one,)))
    assert "examples: packages/one skips (python kind runs none)" in (
        capsys.readouterr().out
    )
