"""The nanobind extension: its declaration, the labels its wheels job reads, its verb.

The refusals first: the extension in the workspace's list, its key in
a package that holds no nanobind, and the labels a package that holds
it gets wrong. Then the declaration, the union of labels, the mount
that adds the wheels job for a workspace whose packages hold
nanobind, and the verb each leg of the job runs.
"""

from __future__ import annotations

import importlib.metadata
import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from livery.extensions.nanobind import _ci, _tasks
from livery.footman import Context
from livery.workshop._packages import Package, discover_packages

# The wheels job is nanobind's, added as the mount adds it.
from workshop_nanobind_declared import nanobind_jobs  # noqa: F401


def _member(root: Path, name: str, contract: str) -> None:
    directory = root / "packages" / name
    (directory / "src").mkdir(parents=True)
    (directory / "workshop.toml").write_text(contract)
    (directory / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\nversion = "0.1.0"\n'
    )


def _native(root: Path, name: str, labels: str = "") -> None:
    """A python-nanobind member listing nothing, with ``[nanobind]`` *labels*."""
    table = f"\n[nanobind]\nwheel-platforms = {labels}\n" if labels else ""
    _member(root, name, f'kind = "python-nanobind"\nname = "{name}"\n{table}')


def _context(**env: str) -> Context:
    """A task context stand-in holding *env*, the one attribute the verb reads."""
    return cast("Context", SimpleNamespace(env=dict(env)))


def _refusal(action: Callable[[], object]) -> str:
    with pytest.raises(BaseException) as caught:
        action()
    return str(caught.value)


def _release_root(root: Path) -> None:
    """*root* as the wheels verb reads it: a contract, and a commit for the epoch."""
    (root / "workshop.toml").write_text(
        '[workspace]\nextensions = []\n\n[forge]\nkind = "github"\n'
    )
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(
        ["git", "commit", "-q", "--allow-empty", "-m", "seed"], cwd=root, check=True
    )


def _leg(
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
    released: tuple[tuple[Package, str], ...],
) -> list[str]:
    """Stand in for the builds, saves and floor legs of a release of *released*.

    Returns the calls, in order, as they happen. A build says whether
    the conan workspace was aside while it ran, and a platform-wheel
    member's build leaves a wheel in its ``dist/``.
    """
    from livery.workshop._backends import _cpp_conan, _python_nanobind

    calls: list[str] = []
    workspace = root / _cpp_conan.WORKSPACE_FILE

    def _build(package: Package, root: Path, *, epoch: int = 0) -> Path:
        del root
        seen = "workspace" if workspace.exists() else "aside"
        calls.append(f"build {package.name} ({seen})")
        assert epoch > 0
        dist = package.directory / "dist"
        dist.mkdir(exist_ok=True)
        if package.kind == "python-nanobind":
            wheel = f"{package.name}-0.1.0-cp314-cp314-manylinux_2_28_x86_64.whl"
            (dist / wheel).write_text("")
        return dist

    def _save(package: Package, version: str, into: Path) -> Path:
        calls.append(f"save {package.name} {version}")
        into.mkdir(parents=True, exist_ok=True)
        return into / f"{package.name}-{version}-linux-x64.tgz"

    def _floors(
        package: Package, root: Path, released: dict[str, str], *, epoch: int = 0
    ) -> None:
        del root, epoch
        calls.append(f"floors {package.name} {sorted(released)}")

    def _discover(root: Path, ref: str) -> tuple[tuple[Package, str], ...]:
        del root, ref
        return released

    monkeypatch.setattr("livery.workshop._lifecycle.build", _build)
    monkeypatch.setattr(_cpp_conan, "save_cache", _save)
    monkeypatch.setattr(_python_nanobind, "floor_legs", _floors)
    monkeypatch.setattr("livery.workshop._publish.discover_release", _discover)
    monkeypatch.chdir(root)
    return calls


# --- refusals first ---------------------------------------------------------


def test_nanobind_in_the_workspace_list_is_named_as_package_level(
    tmp_path: Path,
) -> None:
    from livery.workshop._extensions import level_problems

    (tmp_path / "workshop.toml").write_text('[workspace]\nextensions = ["nanobind"]\n')
    assert level_problems(tmp_path) == [
        "[workspace] extensions lists nanobind, which is listed in a package's"
        " `extensions` alone; move it there"
    ]


def test_the_key_in_a_package_that_holds_no_nanobind_refuses_naming_nanobind(
    tmp_path: Path,
) -> None:
    (tmp_path / "workshop.toml").write_text("[workspace]\nextensions = []\n")
    _member(
        tmp_path,
        "lib",
        'kind = "python"\nname = "lib"\n\n[nanobind]\nwheel-platforms = ["x"]\n',
    )
    # The contract's judge refuses it: the package holds python alone.
    message = _refusal(lambda: _ci.wheel_runners(tmp_path))
    assert "nanobind is a key of nanobind, which this package's `extensions`" in (
        message
    )
    # A contract read with every installed extension taken as listed, as
    # one with no root contract above it is, refuses at the labels' read.
    (tmp_path / "workshop.toml").unlink()
    assert _refusal(lambda: _ci.wheel_runners(tmp_path)) == (
        "packages/lib: [nanobind] wheel-platforms is declared on a package that"
        " holds no nanobind, and no wheels job builds its wheel; remove the key"
    )


def test_a_package_that_holds_nanobind_without_the_key_refuses_naming_it(
    tmp_path: Path,
) -> None:
    _native(tmp_path, "ext")
    assert _refusal(lambda: _ci.wheel_runners(tmp_path)) == (
        "packages/ext: [nanobind] wheel-platforms is missing: a package that holds"
        ' nanobind names the runner labels that build its wheels, like ["ubuntu-'
        'latest", "macos-latest", "windows-latest"]'
    )


def test_an_empty_list_or_a_blank_label_refuses(tmp_path: Path) -> None:
    _native(tmp_path, "ext", "[]")
    assert _refusal(lambda: _ci.wheel_runners(tmp_path)) == (
        "packages/ext: [nanobind] wheel-platforms must be a non-empty list of"
        ' runner labels, like ["ubuntu-latest"]'
    )
    contract = tmp_path / "packages" / "ext" / "workshop.toml"
    contract.write_text(
        'kind = "python-nanobind"\nname = "ext"\n\n[nanobind]\n'
        'wheel-platforms = ["ubuntu-latest", " "]\n'
    )
    assert _refusal(lambda: _ci.wheel_runners(tmp_path)) == (
        "packages/ext: [nanobind] wheel-platforms entry ' ' is not a runner label"
    )
    # The contract's judge holds the key to a list of strings.
    contract.write_text(
        'kind = "python-nanobind"\nname = "ext"\n\n[nanobind]\n'
        'wheel-platforms = "ubuntu-latest"\n'
    )
    assert "nanobind.wheel-platforms is a string ('ubuntu-latest')" in _refusal(
        lambda: _ci.wheel_runners(tmp_path)
    )


def test_the_verb_refuses_outside_a_workspace_and_without_a_host_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace

    from livery.workshop import _kinds

    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)
    ctx = _context()
    assert _refusal(lambda: _tasks.nanobind_wheels(ctx)) == (
        "no workspace: no workshop.toml above the working directory"
    )
    root = tmp_path / "root"
    root.mkdir()
    _release_root(root)
    monkeypatch.chdir(root)
    record = _kinds.kind_for("python-nanobind")
    monkeypatch.setitem(
        _kinds._KINDS, "python-nanobind", replace(record, host_build=None)
    )
    assert _refusal(lambda: _tasks.nanobind_wheels(ctx)) == (
        "kind 'python-nanobind' names no host build, so the wheels job's leg has"
        " nothing to run; the kind's registration names one"
    )


# --- the declaration ----------------------------------------------------------


def test_the_wheel_ships_nanobind_declaring_its_key_its_job_and_its_verb() -> None:
    from livery.workshop._contract_keys import declarations
    from livery.workshop._declaration import Reference, defined_tasks
    from livery.workshop._extensions import declaration

    found = {
        entry.name: entry.value
        for entry in importlib.metadata.entry_points(group="workshop.extensions")
    }
    assert found["nanobind"] == "livery.extensions.nanobind"
    plugins = {
        entry.name: entry.value
        for entry in importlib.metadata.entry_points(group="footman.tasks")
    }
    assert plugins["livery.extensions.nanobind"] == "livery.extensions.nanobind._tasks"
    declared = declaration("nanobind")
    assert declared is not None
    assert declared.levels == ("package",)
    assert declared.requires == ()
    assert declared.plugin == "livery.extensions.nanobind"
    assert declared.additions.steps == ()
    # The key a package that holds nanobind names its runners under.
    owned = declarations()[("package", "nanobind.wheel-platforms")]
    assert (owned.owner, owned.declared.types) == ("nanobind", ("strs",))
    # The wheels job, and the publish job's wait on it.
    wheels, publish = declared.additions.jobs
    assert (wheels.point, wheels.job.name, wheels.job.joins) == (
        "release",
        "wheels",
        False,
    )
    assert wheels.job.matrix == "runners"
    assert wheels.job.labels == Reference(
        "livery.extensions.nanobind._ci", "wheel_runners"
    )
    assert (wheels.job.fetch, wheels.job.publishes, wheels.job.conan_cache) == (
        "full",
        "wheels",
        True,
    )
    assert wheels.job.paths == ("packages/*/dist/*", "packages/*/*/dist/*")
    assert wheels.job.note == (
        "Every platform's wheels, built before the wave: the matrix feeds the"
        " publish job through artifacts, so one release ships the complete set."
    )
    # The verb takes no ref: the job checks out the release squash.
    assert [(e.task, e.args, e.source) for e in wheels.entries] == [
        ("nanobind.wheels", (), "nanobind")
    ]
    assert "nanobind.wheels" in defined_tasks(Path(_tasks.__file__).parent)
    assert (publish.point, publish.job.name, publish.job.joins) == (
        "release",
        "publish",
        True,
    )
    assert (publish.job.collects, publish.job.needs, publish.entries) == (
        "wheels",
        ("wheels",),
        (),
    )


def test_a_pure_workspace_names_no_wheel_runners(tmp_path: Path) -> None:
    _member(tmp_path, "lib", 'kind = "python"\nname = "lib"\n')
    assert _ci.wheel_runners(tmp_path) == []
    (package,) = discover_packages(tmp_path)
    assert _ci.declared_wheel_platforms(package) == []
    assert not _ci.holds_nanobind(package)


def test_the_labels_are_the_union_in_declaration_order(tmp_path: Path) -> None:
    _native(tmp_path, "alpha", '["ubuntu-latest", "macos-latest"]')
    _native(tmp_path, "beta", '["windows-latest", "ubuntu-latest"]')
    _member(tmp_path, "lib", 'kind = "python"\nname = "lib"\n')
    # A package that lists nanobind holds it as one of the kind does.
    _member(
        tmp_path,
        "listed",
        'kind = "python-nanobind"\nname = "listed"\nextensions = ["nanobind"]\n\n'
        '[nanobind]\nwheel-platforms = ["linux-arm"]\n',
    )
    assert _ci.wheel_runners(tmp_path) == [
        "ubuntu-latest",
        "macos-latest",
        "windows-latest",
        "linux-arm",
    ]


@pytest.mark.usefixtures("nanobind_jobs")
def test_the_emitted_wheels_job_runs_the_declared_labels(tmp_path: Path) -> None:
    from livery.workshop._ci_generate import _actions_workflow, _facts

    (tmp_path / "workshop.toml").write_text(
        '[workspace]\nextensions = []\n\n[forge]\nkind = "github"\n'
        'owner = "owner"\n\n[ci]\nrunners = ["ubuntu-latest", "macos-latest"]\n'
        'required-context = "gate"\n'
    )
    _native(tmp_path, "ext", '["ubuntu-latest"]')
    facts = _facts(tmp_path)
    assert facts["job_runners"] == {("release", "wheels"): ("ubuntu-latest",)}
    github = _actions_workflow(facts, "fm", "release.yml", forge="github")
    assert "os: [ubuntu-latest]" in github and "--prebuilt" not in github
    gitea = _actions_workflow(facts, "fm", "release.yml", forge="gitea")
    # The legs follow the declaration, not the check runners.
    assert (
        "os: [ubuntu-latest]" in gitea
        and "macos-latest" not in gitea.split("publish:")[0]
    )


def test_the_mount_adds_the_wheels_job_where_a_package_holds_nanobind(
    tmp_path: Path,
) -> None:
    from livery.footman import _registry as registry
    from livery.workshop import _points, _steps
    from livery.workshop._extensions import mount_extensions, package_extensions
    from workshop_docs_declared import jobs_restored

    pure = tmp_path / "pure"
    pure.mkdir()
    (pure / "workshop.toml").write_text("[workspace]\nextensions = []\n")
    _member(pure, "lib", 'kind = "python"\nname = "lib"\n')
    native = tmp_path / "native"
    native.mkdir()
    (native / "workshop.toml").write_text("[workspace]\nextensions = []\n")
    _native(native, "ext", '["ubuntu-latest"]')
    # The fallback first: a workspace whose packages hold no nanobind
    # mounts none, and its release is the publish job alone.
    assert "nanobind" not in package_extensions(pure)
    # A package that lists nothing holds what its kind stands for.
    assert "nanobind" in package_extensions(native)
    state = _steps.snapshot()
    try:
        for root, jobs in ((pure, ("publish",)), (native, ("wheels", "publish"))):
            with jobs_restored(), registry.capture():
                _points.restore(_points.snapshot(initial=True))
                mount_extensions(root)
                assert _points.jobs_of(root, "release") == jobs
    finally:
        _steps.restore(state)


# --- the verb -------------------------------------------------------------------


def test_the_build_set_names_the_matrix_interpreters() -> None:
    assert _tasks.cibw_build_set(["3.11", "3.14"]) == "cp311-* cp314-*"
    assert _tasks.cibw_build_set(["3.14t"]) == "cp314t-*"
    assert _tasks.cibw_build_set([]) == ""


def test_a_release_with_no_native_member_builds_nothing_and_stays_green(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _release_root(tmp_path)
    _member(tmp_path, "lib", 'kind = "python"\nname = "lib"\n')
    (package,) = discover_packages(tmp_path)
    calls = _leg(monkeypatch, tmp_path, ((package, "0.1.0"),))
    ctx = _context()
    _tasks.nanobind_wheels(ctx)
    # No conan create, no wheel, no floor leg.
    assert calls == []
    out = capsys.readouterr().out
    assert "no native members in this release; nothing to build" in out


def test_a_release_of_conan_members_alone_creates_and_saves_each_with_no_floor_leg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each conan member is created and saved while the workspace is aside.

    No member builds a platform wheel, so no floor leg runs: a floor
    leg builds a wheel against a conan member's floor.
    """
    from livery.workshop._backends import _cpp_conan

    _release_root(tmp_path)
    for name in ("geometry", "mesh"):
        _member(tmp_path, name, f'kind = "cpp-conan"\nname = "{name}"\n')
    listed = "packages:\n  - path: packages/geometry\n  - path: packages/mesh\n"
    workspace = tmp_path / _cpp_conan.WORKSPACE_FILE
    workspace.write_text(listed)
    geometry, mesh = discover_packages(tmp_path)
    calls = _leg(monkeypatch, tmp_path, ((geometry, "0.1.0"), (mesh, "0.2.0")))
    ctx = _context()
    _tasks.nanobind_wheels(ctx)
    assert calls == [
        "build geometry (aside)",
        "save geometry 0.1.0",
        "build mesh (aside)",
        "save mesh 0.2.0",
    ]
    # The leg puts the workspace back for whatever runs after it.
    assert workspace.read_text() == listed


def test_the_wheels_verb_sets_the_build_set_on_the_task_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _release_root(tmp_path)
    _native(tmp_path, "ext", '["ubuntu-latest"]')
    (package,) = discover_packages(tmp_path)
    calls = _leg(monkeypatch, tmp_path, ((package, "0.1.0"),))
    ctx = _context()
    _tasks.nanobind_wheels(ctx)
    # No root pyproject: the matrix is the default floor and the newest minor.
    assert ctx.env == {"CIBW_BUILD": "cp311-* cp314-*", "CIBW_SKIP": ""}
    assert "CIBW_BUILD" not in os.environ
    assert calls == ["build ext (aside)", "floors ext ['packages/ext']"]
    # A build set the caller names wins.
    ctx = _context(CIBW_BUILD="cp314-*")
    _tasks.nanobind_wheels(ctx)
    assert ctx.env == {"CIBW_BUILD": "cp314-*", "CIBW_SKIP": ""}


def test_the_leg_creates_the_conan_member_before_the_wheels_and_proves_the_floors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The leg's three steps, in the one order that works.

    The extension resolves the library from this leg's own conan
    cache, so the library is created first; its cache is saved for
    the wave to attach; and the floors are proved once the wheels
    the release ships are built.
    """
    from livery.workshop._backends import _cpp_conan

    _release_root(tmp_path)
    _member(tmp_path, "geometry", 'kind = "cpp-conan"\nname = "geometry"\n')
    _native(tmp_path, "ext", '["ubuntu-latest"]')
    members = {p.name: p for p in discover_packages(tmp_path)}
    workspace = tmp_path / _cpp_conan.WORKSPACE_FILE
    workspace.write_text("packages:\n  - path: packages/geometry\n")
    calls = _leg(
        monkeypatch,
        tmp_path,
        ((members["geometry"], "0.1.0"), (members["ext"], "0.2.0")),
    )
    ctx = _context()
    _tasks.nanobind_wheels(ctx)
    assert calls == [
        "build geometry (aside)",
        "save geometry 0.1.0",
        "build ext (aside)",
        "floors ext ['packages/ext', 'packages/geometry']",
    ]
    # The leg puts the workspace back for whatever runs after it.
    assert workspace.read_text() == "packages:\n  - path: packages/geometry\n"
