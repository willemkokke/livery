"""The nanobind extension: its distribution, its declaration, its wheels job.

The refusals first: the extension in the workspace's list, its key in
a package that holds no nanobind, and the labels a package that holds
it gets wrong; a workspace without nanobind names no member its job
builds and installs nothing of it. Then its own distribution, the
declaration, the plugin, the mount and the kit, the union of labels,
the members the wheels job builds, the mount that adds the wheels job
for a workspace whose packages hold nanobind, and the verb each leg of
the job runs.
"""

from __future__ import annotations

import importlib.metadata
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

# The wheels job is nanobind's, added as the mount adds it.
from extensions_nanobind_workspace import (
    nanobind_jobs,  # noqa: F401
    skip_unless_installed,
)
from livery.extensions.nanobind import _ci, _tasks
from livery.footman import Context, Group
from livery.workshop._packages import Package, discover_packages
from livery.workshop.testing import CLAUSES, Clause


def _member(root: Path, name: str, contract: str) -> None:
    directory = root / "packages" / name
    (directory / "src").mkdir(parents=True)
    (directory / "workshop.toml").write_text(contract)
    (directory / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\nversion = "0.1.0"\n'
    )


#: The lines a member listing nanobind opens its contract with.
NATIVE = 'extensions = ["nanobind"]\n'


def _native(root: Path, name: str, labels: str = "") -> None:
    """A member listing nanobind, with ``[nanobind]`` *labels*."""
    table = f"\n[nanobind]\nwheel-platforms = {labels}\n" if labels else ""
    _member(root, name, f'{NATIVE}name = "{name}"\n{table}')


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
    from livery.extensions.conan import _package as _conan

    calls: list[str] = []
    workspace = root / _conan.WORKSPACE_FILE

    def _build(package: Package, root: Path, *, epoch: int = 0) -> Path:
        del root
        seen = "workspace" if workspace.exists() else "aside"
        calls.append(f"build {package.name} ({seen})")
        assert epoch > 0
        dist = package.directory / "dist"
        dist.mkdir(exist_ok=True)
        if "nanobind" in package.extensions:
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

    monkeypatch.setattr("livery.workshop._lifecycle.build_release", _build)
    monkeypatch.setattr(_conan, "save_cache", _save)
    monkeypatch.setattr(_ci, "floor_legs", _floors)
    monkeypatch.setattr("livery.workshop._publish.discover_release", _discover)
    monkeypatch.chdir(root)
    return calls


# --- refusals first ---------------------------------------------------------


def test_the_key_in_a_package_that_holds_no_nanobind_refuses_naming_nanobind(
    tmp_path: Path,
) -> None:
    (tmp_path / "workshop.toml").write_text("[workspace]\nextensions = []\n")
    _member(
        tmp_path,
        "lib",
        'extensions = ["python"]\nname = "lib"\n\n'
        '[nanobind]\nwheel-platforms = ["x"]\n',
    )
    # The contract's judge refuses it: the package holds python alone.
    message = _refusal(lambda: _ci.wheel_runners(tmp_path))
    assert (
        "nanobind is a key of nanobind, which neither [workspace] extensions nor"
        " this package's `extensions`"
    ) in message
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
        f'{NATIVE}name = "ext"\n\n[nanobind]\n'
        'wheel-platforms = ["ubuntu-latest", " "]\n'
    )
    assert _refusal(lambda: _ci.wheel_runners(tmp_path)) == (
        "packages/ext: [nanobind] wheel-platforms entry ' ' is not a runner label"
    )
    # The contract's judge holds the key to a list of strings.
    contract.write_text(
        f'{NATIVE}name = "ext"\n\n[nanobind]\nwheel-platforms = "ubuntu-latest"\n'
    )
    assert "nanobind.wheel-platforms is a string ('ubuntu-latest')" in _refusal(
        lambda: _ci.wheel_runners(tmp_path)
    )


def test_the_verb_refuses_outside_a_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)
    ctx = _context()
    assert _refusal(lambda: _tasks.nanobind_wheels(ctx)) == (
        "no workspace: no workshop.toml above the working directory"
    )


def test_a_workspace_without_nanobind_names_no_job_built_member(
    tmp_path: Path,
) -> None:
    from livery.workshop._queries import JOB_BUILT_MEMBERS, answer

    (tmp_path / "workshop.toml").write_text("[workspace]\nextensions = []\n")
    _member(tmp_path, "lib", 'extensions = ["python"]\nname = "lib"\n')
    # A conan library is a member the wheels job builds where the job
    # exists; without a package that holds nanobind there is no job.
    _member(
        tmp_path, "geometry", 'extensions = ["cmake", "conan"]\nname = "geometry"\n'
    )
    assert answer(tmp_path, JOB_BUILT_MEMBERS) == {}
    # Asked directly, nanobind's answer names none either.
    assert _ci.job_built_members(tmp_path) == {}


def _composed_dev_group(root: Path) -> list[str]:
    """The dev group of the project file the engine composes for *root*."""
    import tomllib

    from livery.workshop._shipped_files import outputs

    (composed,) = [o for o in outputs(root) if o.path == "pyproject.toml"]
    group: list[str] = tomllib.loads(composed.body.decode())["dependency-groups"]["dev"]
    return group


def test_a_workspace_without_nanobind_installs_none(tmp_path: Path) -> None:
    # Unlisted, neither the extension's distribution nor nanobind itself
    # reaches the venv, and the workshop's wheel requires neither.
    (tmp_path / "workshop.toml").write_text(
        '[workspace]\nname = "acme"\nextensions = []\n'
    )
    _member(tmp_path, "lib", 'extensions = ["python"]\nname = "lib"\n')
    assert [line for line in _composed_dev_group(tmp_path) if "nanobind" in line] == []
    required = importlib.metadata.requires("livery-workshop") or []
    assert [line for line in required if "nanobind" in line] == []
    # A package that lists it brings its distribution and the venv's
    # nanobind, which an editor's configure of the module reads.
    _native(tmp_path, "ext", '["ubuntu-latest"]')
    dev = _composed_dev_group(tmp_path)
    assert "livery-extensions-nanobind" in dev
    assert "nanobind>=2.7" in dev


def _mounted_tree(root: Path) -> Group:
    """The tree a process started now at *root* mounts, the templates' verbs in it."""
    from livery.footman import _registry as registry
    from livery.workshop import _points
    from livery.workshop._extensions import mount_extensions

    _points.restore(_points.snapshot(initial=True))
    with registry.capture() as tree:
        mount_extensions(root)
    return tree


def test_a_workspace_that_lists_no_nanobind_has_no_verb_for_its_template(
    tmp_path: Path,
) -> None:
    from livery.footman.testing import Runner

    (tmp_path / "workshop.toml").write_text(
        '[workspace]\nname = "acme"\nnamespace = "acme"\nextensions = ["python"]\n'
    )
    # The refusal first: nothing lists nanobind, so no verb creates its
    # template, and footman refuses a task it does not know.
    refused = Runner().invoke(
        "new.package.nanobind-module native",
        tasks=_mounted_tree(tmp_path),
        cwd=tmp_path,
    )
    assert refused.exit_code == 64, refused.stderr
    assert "no task named 'new.package.nanobind-module'" in refused.stderr
    assert not (tmp_path / "packages").exists()
    # Listed, the template's verb exists.
    (tmp_path / "workshop.toml").write_text(
        '[workspace]\nname = "acme"\nnamespace = "acme"\nextensions = ["nanobind"]\n'
    )
    verbs = _mounted_tree(tmp_path).groups["new"].groups["package"]
    assert "nanobind-module" in verbs.tasks


# --- the declaration ----------------------------------------------------------


def _floor_cut(requirement: str) -> str:
    """*requirement* with its floor's version written ``<floor>``.

    A release raises a floor on a member released beside it to that
    member's new version, so the value is the release's to move. The
    name, the floor's presence and the marker stay as the wheel wrote
    them.
    """
    return re.sub(r">=\s*\d[^\s,;]*", ">=<floor>", requirement)


def test_its_own_distribution_ships_nanobind_and_the_workshop_wheel_none() -> None:
    from livery.workshop._extensions import declaration, shipping_distribution

    ours = {
        (entry.group, entry.name, entry.value)
        for entry in importlib.metadata.distribution(
            "livery-extensions-nanobind"
        ).entry_points
    }
    assert ours == {
        ("workshop.extensions", "nanobind", "livery.extensions.nanobind"),
        (
            "footman.tasks",
            "livery.extensions.nanobind",
            "livery.extensions.nanobind._tasks",
        ),
    }
    theirs = importlib.metadata.distribution("livery-workshop").entry_points
    assert [
        entry.name
        for entry in theirs
        if entry.value.startswith("livery.extensions.nanobind")
    ] == []
    shipped = shipping_distribution("livery.extensions.nanobind")
    assert shipped is not None and shipped.name == "livery-extensions-nanobind"
    # The extensions it requires, python, cpp and conan, each ship in a
    # distribution of its own, and this wheel requires all three beside
    # the base, each at a floor.
    required = importlib.metadata.requires("livery-extensions-nanobind") or []
    assert [
        _floor_cut(line)
        for line in required
        if line.startswith(("livery-workshop", "livery-extensions-"))
    ] == [
        "livery-extensions-conan>=<floor>",
        "livery-extensions-cpp>=<floor>",
        "livery-extensions-python>=<floor>",
        "livery-workshop>=<floor>",
    ]
    declared = declaration("nanobind")
    assert declared is not None
    assert {
        name: getattr(shipping_distribution(f"livery.extensions.{name}"), "name", "")
        for name in declared.requires
    } == {
        "python": "livery-extensions-python",
        "cpp": "livery-extensions-cpp",
        "conan": "livery-extensions-conan",
    }


def test_the_local_act_refuses_an_edit_to_the_recipe_the_stamp_writes(
    tmp_path: Path,
) -> None:
    from livery.workshop import _lifecycle
    from livery.workshop._release_driver import local_release, resolve_set

    skip_unless_installed("nanobind")
    # The refusal first: the recipe names no version, and nanobind's
    # stamp raises its floors, so an edit of its own there would be lost
    # to the restore after the act.
    root = tmp_path / "ws"
    root.mkdir()
    (root / "workshop.toml").write_text("[workspace]\n")
    _native(root, "ext")
    recipe = root / "packages" / "ext" / "conanfile.py"
    recipe.write_text('requires = ("fmt/[>=10.0]",)\n')
    for args in (
        ("init", "-q", "--initial-branch=main"),
        ("add", "-A"),
        ("-c", "commit.gpgsign=false", "commit", "-q", "-m", "chore: seed"),
    ):
        subprocess.run(["git", *args], cwd=root, capture_output=True, check=True)
    (member,) = resolve_set(root, ("ext",))
    # nanobind names the recipe among the files the stamp phase writes.
    assert recipe in _lifecycle.version_files(member)
    recipe.write_text(recipe.read_text() + "# by hand\n")
    with pytest.raises(BaseException) as caught:
        local_release(root, (member,))
    said = str(caught.value)
    assert "packages/ext/conanfile.py" in said
    assert "Commit or stash them first" in said
    assert recipe.read_text().endswith("# by hand\n")


def test_nanobind_declares_its_key_its_job_and_its_verb() -> None:
    from livery.workshop._contract_keys import declarations
    from livery.workshop._declaration import DeclaredValue, Reference, defined_tasks
    from livery.workshop._extensions import declaration

    declared = declaration("nanobind")
    assert declared is not None
    # The distribution, the extension module's language and its provider.
    assert declared.requires == ("python", "cpp", "conan")
    assert declared.plugin == "livery.extensions.nanobind"
    assert declared.tools == ("cmake", "ninja", "conan", "cmake_conan")
    # Its steps stamp and build a package's release, which the gate never
    # runs.
    assert declared.additions.steps == ()
    (stamp,) = declared.phases["stamp"]
    assert (stamp.name, str(stamp.run)) == (
        "stamp.nanobind",
        "livery.extensions.nanobind._stamp:stamp",
    )
    assert (stamp.reads, stamp.provides) == (
        ("version", "floors"),
        (("changed", "strs"),),
    )
    # One step of a package reports what the stamp changed, so python's
    # and conan's stamp steps go where nanobind is held.
    assert [address for address, _reason in stamp.deletes] == [
        "python:stamp.python",
        "conan:stamp.conan",
    ]
    (build,) = declared.phases["build"]
    assert (build.name, str(build.run), build.isolated) == (
        "build.nanobind",
        "livery.extensions.nanobind._build:build",
        True,
    )
    assert (build.reads, build.provides) == (("epoch",), (("dist", "path"),))
    assert [address for address, _reason in build.deletes] == [
        "python:build.python",
        "conan:build.conan",
        "conan:publish.conan",
    ]
    assert list(declared.phases) == ["stamp", "build"]
    # The members its wheels job builds, a question about the workspace,
    # and the venv's line an editor's configure of the module reads.
    assert declared.queries == {
        "job-built-members": Reference(
            "livery.extensions.nanobind._ci", "job_built_members"
        ),
        # The recipe the stamp raises floors in, among the files the
        # stamp phase writes.
        "version-files": Reference(
            "livery.extensions.nanobind._stamp", "version_files"
        ),
        "python.dev-group": DeclaredValue(["nanobind>=2.7"]),
    }
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


def test_a_package_listing_nanobind_releases_through_its_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Python's version, stamp and publish, nanobind's build, and none of conan's.

    The member's recipe is a consumer's, so conan answers no version for
    it, and nanobind deletes conan's stamp, build and publish and
    python's build in every set that holds it: any of them running would
    fail here.
    """
    from livery.extensions.conan import _package as conan_package
    from livery.extensions.nanobind import _build
    from livery.extensions.python import _backend as python_backend
    from livery.workshop import _lifecycle
    from livery.workshop._registries import RegistryTarget

    (tmp_path / "workshop.toml").write_text("[workspace]\nextensions = []\n")
    _native(tmp_path, "ext", '["ubuntu-latest"]')
    directory = tmp_path / "packages" / "ext"
    (directory / "conanfile.py").write_text(
        'class Ext:\n    requires = ("fmt/[>=11.0]",)\n'
    )
    (package,) = discover_packages(tmp_path)

    def _never(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("a step the set deletes ran")

    for module, name in (
        (conan_package, "stamp_version"),
        (conan_package, "build"),
        (conan_package, "publish_artifact"),
        (python_backend, "build"),
    ):
        monkeypatch.setattr(module, name, _never)
    built: list[int] = []

    def _build_dist(member: Package, root: Path, *, epoch: int = 0) -> Path:
        del root
        built.append(epoch)
        return member.directory / "dist"

    published: list[tuple[str, str]] = []

    def _publish(
        member: Package, root: Path, *, version: str, target: RegistryTarget
    ) -> bool:
        del member, root
        published.append((version, target.url))
        return True

    monkeypatch.setattr(_build, "build_dist", _build_dist)
    monkeypatch.setattr(python_backend, "publish_artifact", _publish)
    assert _lifecycle.current_version(package) == "0.1.0"
    # python's pyproject, and nanobind's answer: the recipe its stamp
    # raises floors in.
    assert _lifecycle.version_files(package) == (
        directory / "pyproject.toml",
        directory / "conanfile.py",
    )
    assert _lifecycle.stamp(package, tmp_path, "0.2.0") == ["pyproject.toml"]
    assert _lifecycle.current_version(package) == "0.2.0"
    assert _lifecycle.build_release(package, tmp_path, epoch=7) == directory / "dist"
    assert built == [7]
    target = RegistryTarget(kind="python", url="https://idx.example/simple")
    assert _lifecycle.publish(package, tmp_path, version="0.2.0", target=target)
    assert published == [("0.2.0", "https://idx.example/simple")]


def test_a_pure_workspace_names_no_wheel_runners(tmp_path: Path) -> None:
    _member(tmp_path, "lib", 'extensions = ["python"]\nname = "lib"\n')
    assert _ci.wheel_runners(tmp_path) == []
    (package,) = discover_packages(tmp_path)
    assert _ci.declared_wheel_platforms(package) == []
    assert not _ci.holds_nanobind(package)


def test_the_labels_are_the_union_in_declaration_order(tmp_path: Path) -> None:
    _native(tmp_path, "alpha", '["ubuntu-latest", "macos-latest"]')
    _native(tmp_path, "beta", '["windows-latest", "ubuntu-latest"]')
    _member(tmp_path, "lib", 'extensions = ["python"]\nname = "lib"\n')
    _native(tmp_path, "listed", '["linux-arm"]')
    assert _ci.wheel_runners(tmp_path) == [
        "ubuntu-latest",
        "macos-latest",
        "windows-latest",
        "linux-arm",
    ]


def _recipe(root: Path, name: str, recipe: str) -> None:
    """Write ``packages/<name>/conanfile.py`` holding *recipe*."""
    (root / "packages" / name / "conanfile.py").write_text(recipe)


def test_the_wheels_job_builds_each_nanobind_member_and_each_conan_package(
    tmp_path: Path,
) -> None:
    from livery.workshop._queries import JOB_BUILT_MEMBERS, answer

    (tmp_path / "workshop.toml").write_text("[workspace]\nextensions = []\n")
    _native(tmp_path, "ext", '["ubuntu-latest"]')
    _member(tmp_path, "plain", 'extensions = ["python"]\nname = "plain"\n')
    # A library listing conan releases the package its recipe names.
    _member(
        tmp_path,
        "mesh",
        'name = "mesh"\nextensions = ["cmake", "conan"]\n',
    )
    _recipe(tmp_path, "mesh", 'class M:\n    name = "mesh"\n    version = "0.1.0"\n')
    # A recipe of requirements alone is a consumer's, and releases nothing.
    _member(
        tmp_path,
        "app",
        'name = "app"\nextensions = ["cmake", "conan"]\n',
    )
    _recipe(tmp_path, "app", 'class A:\n    requires = ("mesh/[>=0.1.0]",)\n')
    built = {"packages/ext": "wheels", "packages/mesh": "wheels"}
    assert _ci.job_built_members(tmp_path) == built
    # The workspace's answer is nanobind's, mounted for the package that
    # holds it.
    assert answer(tmp_path, JOB_BUILT_MEMBERS) == built


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
    from extensions_nanobind_workspace import jobs_restored
    from livery.footman import _registry as registry
    from livery.workshop import _points, _steps
    from livery.workshop._extensions import installed_set, mount_extensions

    pure = tmp_path / "pure"
    pure.mkdir()
    (pure / "workshop.toml").write_text("[workspace]\nextensions = []\n")
    _member(pure, "lib", 'extensions = ["python"]\nname = "lib"\n')
    native = tmp_path / "native"
    native.mkdir()
    (native / "workshop.toml").write_text("[workspace]\nextensions = []\n")
    _native(native, "ext", '["ubuntu-latest"]')
    # The fallback first: a workspace whose packages hold no nanobind
    # mounts none, and its release is the publish job alone.
    assert "nanobind" not in installed_set(pure)
    # A package that lists nanobind holds it.
    assert "nanobind" in installed_set(native)
    state = _steps.snapshot()
    try:
        for root, jobs in ((pure, ("publish",)), (native, ("wheels", "publish"))):
            with jobs_restored(), registry.capture():
                _points.restore(_points.snapshot(initial=True))
                mount_extensions(root)
                assert _points.jobs_of(root, "release") == jobs
    finally:
        _steps.restore(state)


def test_the_plugin_loaded_alone_registers_the_nanobind_group_alone() -> None:
    # The task reference renders each provider's tree with nothing else
    # mounted: the plugin's tree holds the nanobind group and no group of
    # the base's, with the wheels verb hidden.
    probe = (
        "import json\n"
        "import livery.footman as footman\n"
        "import livery.extensions.nanobind._tasks\n"
        "tree = footman.task_tree()\n"
        "group = tree['groups']['nanobind']\n"
        "print(json.dumps([sorted(tree['groups']), sorted(tree['tasks']),"
        " sorted(group['tasks'])]))\n"
    )
    run = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=False
    )
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout) == [["nanobind"], [], ["wheels"]]


def _mount(root: Path) -> tuple[str, ...]:
    """Mount what *root*'s lists hold, as a verb there would; the names mounted."""
    from livery.footman import _registry as registry
    from livery.workshop._extensions import mount_extensions

    with registry.capture():
        return mount_extensions(root)


def test_a_package_listing_nanobind_mounts_it_with_what_it_requires(
    tmp_path: Path,
) -> None:
    (tmp_path / "workshop.toml").write_text("[workspace]\nextensions = []\n")
    _native(tmp_path, "ext", '["ubuntu-latest"]')
    assert _mount(tmp_path) == ("cpp", "conan", "python", "nanobind")


@pytest.mark.parametrize("clause", CLAUSES, ids=lambda clause: clause.name)
def test_nanobind_conforms(clause: Clause, tmp_path: Path) -> None:
    from livery.workshop._steps import StepRecord, steps_by_name
    from livery.workshop.testing import Subject

    (tmp_path / "workshop.toml").write_text("[workspace]\nextensions = []\n")
    _native(tmp_path, "ext", '["ubuntu-latest"]')
    _mount(tmp_path)
    # nanobind registers no gate step: its one step builds a release, on
    # the build phase's isolated side.
    steps: tuple[StepRecord, ...] = tuple(
        record for record in steps_by_name().values() if record.extension == "nanobind"
    )
    assert steps == ()
    subject = Subject("livery.extensions.nanobind", sets=(("nanobind",),))
    assert clause.judge(subject) == []


def test_the_loop_s_dev_wheels_hold_this_distribution() -> None:
    # The loop's plain pass creates loop-native from nanobind-module, so
    # its stack lists nanobind, and the dev wheels the pass builds and
    # publishes before the birth hold this distribution: the newborn
    # installs it, and its wheels job runs nanobind.wheels from it.
    from livery.workshop._e2e import LOOP_MEMBERS, dev_members, loop_stack
    from livery.workshop._extensions import installed_set

    root = Path(__file__).resolve().parents[4]
    # The dev wheels are read from this checkout's packages, whose
    # contracts read right only beside every extension the workspace
    # mounts.
    skip_unless_installed(*installed_set(root))
    templates = {member.name: member.template for member in LOOP_MEMBERS}
    assert templates["loop-native"] == "nanobind-module"
    assert "nanobind" in loop_stack()
    assert "extensions/nanobind" in dev_members(root, loop_stack())


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
    _member(tmp_path, "lib", 'extensions = ["python"]\nname = "lib"\n')
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
    from livery.extensions.conan import _package as _conan

    _release_root(tmp_path)
    for name in ("geometry", "mesh"):
        _member(tmp_path, name, f'extensions = ["cmake", "conan"]\nname = "{name}"\n')
        _recipe(
            tmp_path, name, f'class R:\n    name = "{name}"\n    version = "0.1.0"\n'
        )
    # The workspace holds nanobind, which the wheels job is for, though
    # this release ships none of it.
    _native(tmp_path, "ext", '["ubuntu-latest"]')
    listed = "packages:\n  - path: packages/geometry\n  - path: packages/mesh\n"
    workspace = tmp_path / _conan.WORKSPACE_FILE
    workspace.write_text(listed)
    members = {package.name: package for package in discover_packages(tmp_path)}
    geometry, mesh = members["geometry"], members["mesh"]
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
    from livery.extensions.conan import _package as _conan

    _release_root(tmp_path)
    _member(
        tmp_path, "geometry", 'extensions = ["cmake", "conan"]\nname = "geometry"\n'
    )
    _recipe(
        tmp_path, "geometry", 'class R:\n    name = "geometry"\n    version = "0.1.0"\n'
    )
    _native(tmp_path, "ext", '["ubuntu-latest"]')
    members = {p.name: p for p in discover_packages(tmp_path)}
    workspace = tmp_path / _conan.WORKSPACE_FILE
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
