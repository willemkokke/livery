"""The release's isolated legs: a built wheel installed in a fresh venv and tested.

The legs run for real on a minimal member, the floor and latest legs
once and the head leg once at a lock's pins; the other tests stub uv,
and force the refusals each leg makes: a toolchain that moves a floor,
a floor no index serves, a head leg with no lock.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from extensions_python_wheels import write_wheel
from extensions_python_workspace import two_members  # noqa: F401
from livery.footman import Failed
from livery.workshop import Package, discover_packages, scripts_dir

ROOT = Path(__file__).resolve().parents[4]
_FAILURES = (SystemExit, Failed)


def test_the_isolated_legs_run_for_real_on_a_dependency_free_member(
    workspace: Path,
) -> None:
    # The one unmocked validation: uv build, two fresh venvs, the
    # installed copy's tests. Everything else in this suite may stub
    # it because this proves the machinery.
    root = workspace
    member = root / "packages" / "core"
    (member / "pyproject.toml").write_text(
        '[project]\nname = "livery-core"\nversion = "0.2.0"\n'
        'requires-python = ">=3.11"\ndependencies = []\n'
        "[build-system]\n"
        'requires = ["uv_build>=0.7"]\nbuild-backend = "uv_build"\n'
        "[tool.uv.build-backend]\n"
        'module-name = "livery.core"\nnamespace = true\n'
    )
    tests = member / "tests"
    tests.mkdir()
    (tests / "test_installed.py").write_text(
        "from livery.core import answer\n\n\n"
        "def test_answer() -> None:\n    assert answer() == 42\n"
    )
    (member / "src" / "livery" / "core" / "answer.py").write_text(
        "def answer() -> int:\n    return 42\n"
    )
    (member / "src" / "livery" / "core" / "__init__.py").write_text(
        'from livery.core.answer import answer\n\n__all__ = ["answer"]\n'
        '__version__ = "0.2.0"\n'
    )
    from livery.extensions.python import _backend
    from livery.workshop._release_driver import MemberPlan, validate_member

    packages = {p.directory.name: p for p in discover_packages(root)}
    plan = MemberPlan(packages["core"], "0.2.0")

    _backend.build(plan.package, root)
    validate_member(root, plan, (member / "dist",))
    assert list((member / "dist").glob("*.whl"))


def test_the_head_leg_runs_for_real_at_the_pins_the_lock_holds(
    workspace: Path,
) -> None:
    # The head leg unmocked: uv locks a workspace whose one dependency
    # the lock holds below its newest release, and the leg installs the
    # built wheel at that pin, where the latest leg takes the newest.
    import livery.toolroom.tools as tools
    from livery.extensions.python import _backend

    root = workspace
    member = root / "packages" / "core"
    (member / "pyproject.toml").write_text(
        '[project]\nname = "livery-core"\nversion = "0.2.0"\n'
        'requires-python = ">=3.11"\ndependencies = ["six>=1.10"]\n'
        "[build-system]\n"
        'requires = ["uv_build>=0.7"]\nbuild-backend = "uv_build"\n'
        "[tool.uv.build-backend]\n"
        'module-name = "livery.core"\nnamespace = true\n'
    )
    (root / "pyproject.toml").write_text(
        '[project]\nname = "ws"\nversion = "0.0.0"\nrequires-python = ">=3.11"\n'
        '[tool.uv]\npackage = false\nconstraint-dependencies = ["six==1.15.0"]\n'
        '[tool.uv.workspace]\nmembers = ["packages/core"]\n'
    )
    locked = tools.uv.opts(cwd=root, nofail=True, recorded=False)("lock")
    assert locked.code == 0, locked.stderr
    core = {p.directory.name: p for p in discover_packages(root)}["core"]
    _backend.build(core, root)
    dists = (member / "dist",)
    head = _backend.run_isolated_test(core, root, release_dirs=dists, resolution="head")
    assert head["six"] == "1.15.0"
    latest = _backend.run_isolated_test(
        core, root, release_dirs=dists, resolution="latest"
    )
    assert latest["six"] != "1.15.0"


PROBE = "leg-probe"
"""The stand-in distribution the isolated legs resolve, built in the test.

A leg that resolves a real distribution reaches an index, so a network
fault fails the test on the fetch rather than on the property it pins.
"""


def _starve(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *versions: str) -> Path:
    """Let the isolated legs resolve from local wheels and nothing else.

    Every install the leg runs carries the index arguments, so replacing
    them with ``--no-index`` and a find-links directory closes each
    reach at once. The toolchain's bare pytest resolves from the same
    directory; a seeded member has no tests directory, so it is never
    run.
    """
    from livery.extensions.python import _backend as _python

    links = tmp_path / "links"
    links.mkdir(exist_ok=True)
    for version in versions:
        write_wheel(links, PROBE, version)
    write_wheel(links, "pytest", "9.0.0")
    monkeypatch.setattr(
        _python,
        "_index_args",
        lambda _root: ("--no-index", f"--find-links={links}"),
    )
    return links


def test_the_toolchain_probe_refuses_a_moved_floor(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The deliberate overlap, forced first: a toolchain pin that
    # names one of the package's own floored dependencies lifts it
    # after the starved install, and the probe must refuse rather
    # than let the floor leg pass against the wrong version.
    from livery.extensions.python import _backend as _python

    root = workspace
    member = root / "packages" / "core"
    (member / "pyproject.toml").write_text(
        '[project]\nname = "livery-core"\nversion = "0.2.0"\n'
        'requires-python = ">=3.11"\n'
        f'dependencies = ["{PROBE}>=24.0"]\n'
        "[build-system]\n"
        'requires = ["uv_build>=0.7"]\nbuild-backend = "uv_build"\n'
        "[tool.uv.build-backend]\n"
        'module-name = "livery.core"\nnamespace = true\n'
    )
    _starve(monkeypatch, tmp_path, "24.0", "25.0")
    packages = {p.directory.name: p for p in discover_packages(root)}
    _python.build(packages["core"], root)

    def _overlapping_pins(_root: Path, scratch: Path) -> Path:
        pins = scratch / "dev-pins.txt"
        pins.write_text(f"{PROBE}==25.0\npytest\n")
        return pins

    monkeypatch.setattr(
        "livery.extensions.python._backend._dev_pins", _overlapping_pins
    )
    # The guard is the backstop behind the filter: with the filter
    # bypassed, an overlapping pin moves the floor and is refused.
    monkeypatch.setattr(
        "livery.extensions.python._backend._pins_without", lambda pins, _r: pins
    )
    with pytest.raises(_FAILURES) as caught:
        _python.run_isolated_test(packages["core"], root, resolution="floor")
    message = str(caught.value)
    assert "moved direct dependencies" in message
    assert f"{PROBE} 24.0 -> 25.0" in message


def test_the_toolchain_pins_leave_what_the_leg_resolved(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The leg's floor survives the toolchain install: the lock's pin
    # on the member's own dependency is dropped from the toolchain,
    # so the floor leg proves the floor and the guard has nothing to
    # refuse.
    from livery.extensions.python import _backend as _python

    root = workspace
    member = root / "packages" / "core"
    (member / "pyproject.toml").write_text(
        '[project]\nname = "livery-core"\nversion = "0.2.0"\n'
        'requires-python = ">=3.11"\n'
        f'dependencies = ["{PROBE}>=24.0"]\n'
        "[build-system]\n"
        'requires = ["uv_build>=0.7"]\nbuild-backend = "uv_build"\n'
        "[tool.uv.build-backend]\n"
        'module-name = "livery.core"\nnamespace = true\n'
    )
    _starve(monkeypatch, tmp_path, "24.0", "25.0")
    packages = {p.directory.name: p for p in discover_packages(root)}
    _python.build(packages["core"], root)

    def _overlapping_pins(_root: Path, scratch: Path) -> Path:
        pins = scratch / "dev-pins.txt"
        pins.write_text(f"{PROBE}==25.0\npytest\n")
        return pins

    monkeypatch.setattr(
        "livery.extensions.python._backend._dev_pins", _overlapping_pins
    )
    resolved = _python.run_isolated_test(packages["core"], root, resolution="floor")
    assert resolved[PROBE] == "24.0"


def test_pins_without_drops_only_what_the_leg_resolved(tmp_path: Path) -> None:
    from livery.extensions.python._backend import _pins_without

    pins = tmp_path / "dev-pins.txt"
    pins.write_text(
        "packaging==25.0\n"
        "pytest==8.4.0\n"
        "PyYAML==6.0.3 ; python_version >= '3.11'\n"
        "pytest-xdist[psutil]==3.6.1\n"
        "\n"
    )
    _pins_without(
        pins, {"packaging": "24.0", "pyyaml": "6.0.3", "Livery-Core": "0.2.0"}
    )
    assert pins.read_text().splitlines() == [
        "pytest==8.4.0",
        "pytest-xdist[psutil]==3.6.1",
        "",
    ]


def test_the_leg_reads_the_repos_declared_indexes(tmp_path: Path) -> None:
    # The fallbacks first: no pyproject, then no declared index, both
    # hand the leg nothing and it resolves from the default alone.
    from livery.extensions.python._backend import _index_args

    assert _index_args(tmp_path) == ()
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
    assert _index_args(tmp_path) == ()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\n'
        "[[tool.uv.index]]\n"
        'name = "loop"\n'
        'url = "http://gitea:3000/api/packages/livery/pypi/simple"\n'
    )
    assert _index_args(tmp_path) == (
        "--index",
        "http://gitea:3000/api/packages/livery/pypi/simple",
    )
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\n'
        "[[tool.uv.index]]\n"
        'url = "https://mirror.example/simple"\n'
        "default = true\n"
    )
    assert _index_args(tmp_path) == (
        "--default-index",
        "https://mirror.example/simple",
    )


def test_the_leg_env_leads_with_its_own_venv_and_drops_the_workspaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.extensions.python._backend import _leg_env

    root = tmp_path / "ws"
    venv = tmp_path / "leg-venv"
    monkeypatch.setenv(
        "PATH",
        os.pathsep.join(
            [str(scripts_dir(root / ".venv")), "/usr/bin", str(root / ".venv2")]
        ),
    )
    monkeypatch.setenv("COVERAGE_PROCESS_START", str(root / "pyproject.toml"))
    monkeypatch.setenv("VIRTUAL_ENV", str(root / ".venv"))
    env = _leg_env(root, venv)
    entries = env["PATH"].split(os.pathsep)
    assert entries[0] == str(scripts_dir(venv))  # the leg's own tools answer first
    assert str(scripts_dir(root / ".venv")) not in entries  # the workspace's drop out
    assert "/usr/bin" in entries  # the system stays
    assert str(root / ".venv2") in entries  # only the venv prefix is scrubbed
    assert env["VIRTUAL_ENV"] == str(venv)
    assert "COVERAGE_PROCESS_START" not in env  # the meter never re-points a leg


def test_a_wheel_without_a_test_extra_installs_plain(tmp_path: Path) -> None:
    # The fallback first: no declared extra, no changed spelling.
    from livery.extensions.python._backend import _install_target

    member = tmp_path / "packages" / "plain"
    member.mkdir(parents=True)
    (member / "pyproject.toml").write_text(
        '[project]\nname = "livery-plain"\nversion = "0.1.0"\ndependencies = []\n'
    )
    package = Package(
        directory=member,
        path="packages/plain",
        name="livery-plain",
        depends=(),
        extensions=("python",),
    )
    wheel = member / "dist" / "livery_plain-0.1.0-py3-none-any.whl"
    assert _install_target(package, wheel) == str(wheel)


def test_a_declared_test_extra_rides_the_leg_install(tmp_path: Path) -> None:
    # The suite the leg runs may need more than the runtime deps; the
    # declared extra names it and the install spelling carries it.
    from livery.extensions.python._backend import _install_target

    member = tmp_path / "packages" / "hosted"
    member.mkdir(parents=True)
    (member / "pyproject.toml").write_text(
        '[project]\nname = "livery-hosted"\nversion = "0.1.0"\n'
        "dependencies = []\n"
        "[project.optional-dependencies]\n"
        'test = ["livery-plain>=0.1.0"]\n'
    )
    package = Package(
        directory=member,
        path="packages/hosted",
        name="livery-hosted",
        depends=(),
        extensions=("python",),
    )
    wheel = member / "dist" / "livery_hosted-0.1.0-py3-none-any.whl"
    target = _install_target(package, wheel)
    assert target.startswith("livery-hosted[test] @ file://")
    assert target.endswith("livery_hosted-0.1.0-py3-none-any.whl")


def test_dev_pins_export_the_locks_resolution_or_none(tmp_path: Path) -> None:
    from livery.extensions.python._backend import _dev_pins

    # The monorepo has a lock and a dev group: the export carries the
    # gate's own pytest pin.
    pins = _dev_pins(ROOT, tmp_path)
    assert pins is not None
    assert "pytest==" in pins.read_text()
    # A bare rig has neither: the leg falls back to bare pytest.
    bare = tmp_path / "bare"
    bare.mkdir()
    assert _dev_pins(bare, tmp_path) is None


def test_an_unresolvable_floor_fails_the_floor_leg_by_name(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A solo release whose declared floor names a version no index
    # serves: the floor leg's install refuses, and the refusal is the
    # release's answer, never a silent pass against the tree.
    from livery.extensions.python import _backend as _python

    root = workspace
    member = root / "packages" / "tool"
    (member / "pyproject.toml").write_text(
        '[project]\nname = "livery-tool"\nversion = "0.2.0"\n'
        'requires-python = ">=3.11"\n'
        'dependencies = ["livery-core>=9.9.9"]\n'
        "[build-system]\n"
        'requires = ["uv_build>=0.7"]\nbuild-backend = "uv_build"\n'
        "[tool.uv.build-backend]\n"
        'module-name = "livery.tool"\nnamespace = true\n'
    )
    _starve(monkeypatch, tmp_path)
    packages = {p.directory.name: p for p in discover_packages(root)}
    _python.build(packages["tool"], root)
    with pytest.raises(_FAILURES) as caught:
        _python.run_isolated_test(packages["tool"], root, resolution="floor")
    assert "isolated install" in str(caught.value)


def test_refresh_args_name_every_wheel_of_the_set_once(tmp_path: Path) -> None:
    # The cold cases first: no dirs, and a dir without wheels, name
    # nothing; a distribution's underscored filename is normalised
    # and a second wheel of the same distribution adds no flag.
    from livery.extensions.python._backend import _refresh_args

    assert _refresh_args(()) == []
    empty = tmp_path / "empty"
    empty.mkdir()
    assert _refresh_args((empty,)) == []
    core = tmp_path / "core"
    core.mkdir()
    (core / "livery_core-0.2.0-py3-none-any.whl").write_bytes(b"")
    (core / "livery_core-0.2.0-cp311-cp311-macosx_14_0_arm64.whl").write_bytes(b"")
    tool = tmp_path / "tool"
    tool.mkdir()
    (tool / "Livery_Tool-0.1.0-py3-none-any.whl").write_bytes(b"")
    assert _refresh_args((tool, empty, core)) == [
        "--refresh-package=livery-core",
        "--refresh-package=livery-tool",
    ]


def test_the_leg_refreshes_the_co_released_members_before_installing(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # uv serves a cached wheel by filename, and a member rebuilt at
    # the same version keeps its filename: the first install carries
    # one refresh flag per co-released distribution, before the
    # find-links dirs are consulted. The stub answers the venv step
    # and refuses the install, so the leg stops after the command
    # under test with its argv recorded.
    from types import SimpleNamespace

    import livery.toolroom.tools as toolroom
    from livery.extensions.python import _backend as _python

    root = workspace
    packages = {p.directory.name: p for p in discover_packages(root)}
    core = packages["core"]
    dist = core.directory / "dist"
    dist.mkdir(exist_ok=True)
    (dist / "livery_core-0.2.0-py3-none-any.whl").write_bytes(b"")
    sibling = root / "packages" / "tool" / "dist"
    sibling.mkdir(parents=True, exist_ok=True)
    (sibling / "livery_tool-0.1.0-py3-none-any.whl").write_bytes(b"")
    commands: list[tuple[str, ...]] = []

    def _opts(**_kwargs: object) -> object:
        def _run(*args: str) -> object:
            commands.append(args)
            code = 0 if args[0] == "venv" else 1
            return SimpleNamespace(code=code, stdout="", stderr="refused\n")

        return _run

    monkeypatch.setattr(toolroom, "uv", SimpleNamespace(opts=_opts))
    with pytest.raises(_FAILURES):
        _python.run_isolated_test(
            core, root, release_dirs=(dist, sibling), resolution="floor"
        )
    install = next(args for args in commands if args[:2] == ("pip", "install"))
    # The floor leg resolves every direct dependency at its floor.
    assert "--resolution=lowest-direct" in install
    assert "--refresh-package=livery-core" in install
    assert "--refresh-package=livery-tool" in install
    # The co-released set's directories are the only place the leg
    # finds a wheel beside the indexes.
    assert [arg for arg in install if arg.startswith("--find-links")] == [
        f"--find-links={dist}",
        f"--find-links={sibling}",
    ]
    # The stub refused the install: nothing after it ran.
    assert commands[-1] is install


def test_the_head_leg_installs_at_the_pins_the_lock_holds(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The refusals first: a word other than the three, and a workspace
    # whose lock uv export cannot read, install nothing. The stub
    # answers the export and the venv, keeps what the install was
    # handed as constraints, and refuses the install, so the leg stops
    # after the command under test.
    from types import SimpleNamespace

    import livery.toolroom.tools as toolroom
    from livery.extensions.python import _backend as _python

    root = workspace
    core = {p.directory.name: p for p in discover_packages(root)}["core"]
    dist = core.directory / "dist"
    dist.mkdir(exist_ok=True)
    (dist / "livery_core-0.2.0-py3-none-any.whl").write_bytes(b"")
    with pytest.raises(_FAILURES) as caught:
        _python.run_isolated_test(core, root, resolution="highest")
    assert str(caught.value) == (
        "livery-core: the isolated test resolves at floor, latest, head, not 'highest'"
    )
    locked = [False]
    commands: list[tuple[str, ...]] = []
    constraints: list[str] = []

    def _opts(**_kwargs: object) -> object:
        def _run(*args: str) -> object:
            commands.append(args)
            if args[0] == "export":
                if not locked[0]:
                    return SimpleNamespace(code=2, stdout="", stderr="no uv.lock\n")
                pins = Path(args[args.index("-o") + 1])
                pins.write_text("packaging==25.0\n-e ./vendored\nidna==3.10\n")
                return SimpleNamespace(code=0, stdout="", stderr="")
            if args[:2] == ("pip", "install"):
                where = Path(args[args.index("--constraint") + 1])
                constraints.append(where.read_text())
            code = 0 if args[0] == "venv" else 1
            return SimpleNamespace(code=code, stdout="", stderr="refused\n")

        return _run

    monkeypatch.setattr(toolroom, "uv", SimpleNamespace(opts=_opts))
    with pytest.raises(_FAILURES) as caught:
        _python.run_isolated_test(core, root, resolution="head")
    assert "the head leg installs at the versions the workspace lock pins" in str(
        caught.value
    )
    assert [args[0] for args in commands] == ["export"]
    # With a lock, every pin it holds constrains the install, the local
    # lines dropped, at the newest each pin allows.
    locked[0] = True
    commands.clear()
    with pytest.raises(_FAILURES):
        _python.run_isolated_test(core, root, resolution="head")
    export = commands[0]
    assert export[0] == "export"
    for flag in ("--frozen", "--all-packages", "--no-emit-workspace", "--no-hashes"):
        assert flag in export
    install = next(args for args in commands if args[:2] == ("pip", "install"))
    assert "--resolution=highest" in install
    assert constraints == ["packaging==25.0\nidna==3.10\n"]


def test_the_toolchain_pins_carry_no_workspace_member(tmp_path: Path) -> None:
    # Members export as workspace-relative paths a scratch venv
    # cannot resolve, and reinstalling them would clobber the starved
    # resolution; the released wheel and its floors are already in.
    from livery.extensions.python._backend import _dev_pins

    pins = _dev_pins(ROOT, tmp_path)
    assert pins is not None
    text = pins.read_text()
    assert "pytest" in text  # the toolchain itself is pinned
    assert "packages/forge" not in text
    assert "packages/workshop" not in text
