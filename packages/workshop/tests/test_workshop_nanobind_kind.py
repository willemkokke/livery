"""nanobind's build: the guard and the seed chain first, then the armed build."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest
from _pytest.outcomes import Skipped  # the skip a fixture raises

import livery.toolroom.tools as tools
from livery.extensions.nanobind import _build, _ci, _container
from livery.extensions.python import _backend as _python
from livery.workshop._identity import project_facts
from livery.workshop._kinds import (
    kind_for,
    kind_names,
    template_chain,
)
from livery.workshop._packages import Package
from workshop_composed import seed_into
from workshop_python_steps import python_steps_fixture  # noqa: F401

_FAILURES = (BaseException,)

ROOT = Path(__file__).resolve().parents[3]


def _toolchain_gap() -> str:
    """Why the armed leg cannot run here; empty when it can."""
    missing = [t for t in ("cmake", "ninja", "cc", "c++") if shutil.which(t) is None]
    if missing:
        return f"host toolchain incomplete: {', '.join(missing)} missing"
    if sys.platform == "linux" and shutil.which("docker") is None:
        return "docker unavailable (cibuildwheel needs it for manylinux)"
    if sys.platform == "win32":
        return "MSVC arming on this runner is unverified"
    return ""


@pytest.fixture
def build_rig(tmp_path: Path) -> None:
    """Skip unless this host can run an armed native build in *tmp_path*.

    A fixture rather than a marker, because the second half of the
    question is about the workspace the test builds in: a temporary
    directory holds no receipts, so conan and the provider have to come
    from the entered environment there. A marker is decided once at
    import, before any workspace exists, and asked the host about the
    compiler alone.
    """
    if gap := _toolchain_gap():
        pytest.skip(gap)
    if gap := _container.conan_tools(tmp_path)[2]:
        pytest.skip(gap)


def _package(
    directory: Path,
    name: str,
    *,
    kind: str = "python",
    extensions: tuple[str, ...] = ("nanobind",),
) -> Package:
    """A package listing nanobind, or the *kind* and *extensions* given."""
    return Package(
        directory=directory,
        path=f"packages/{directory.name}",
        name=name,
        kind=kind,
        depends=(),
        extensions=extensions,
    )


def _render_named(tmp_path: Path, package_name: str) -> Path:
    """The nanobind seeds written for *package_name*, native configs composed."""
    answers = project_facts(ROOT)
    destination = tmp_path / "packages" / "native"
    seed_into(
        destination,
        "package-python-nanobind",
        {
            "package_name": package_name,
            "package_description": "A native extension.",
            "namespace_package": "acme",
            "author_name": answers["author_name"],
            "author_email": answers["author_email"],
            "copyright_year": answers["copyright_year"],
            "project_name": "acme",
        },
    )
    # The native configs come from the fragment engine, as a sync writes
    # them into the package of a workspace.
    from livery.workshop._shipped_files import deliver

    contract = tmp_path / "workshop.toml"
    if not contract.is_file():
        contract.write_text("[workspace]\nextensions = []\n")
    deliver(tmp_path)
    return destination


@pytest.mark.parametrize("package_name", ["ci-e2e-loop-loop-native", "x-y"])
def test_no_rendered_python_line_is_over_the_column_limit_whatever_the_name(
    tmp_path: Path, package_name: str
) -> None:
    """A long or short package name lands in a literal on a line of its own."""
    rendered = _render_named(tmp_path, package_name)
    over = [
        (path.name, line)
        for path in rendered.rglob("*.py")
        for line in path.read_text("utf-8").splitlines()
        if len(line) > 88
    ]
    assert over == []


def _render_chain(tmp_path: Path) -> Package:
    """A nanobind package's seeds, python's then the leaf's."""
    destination = tmp_path / "packages" / "ext"
    answers = project_facts(ROOT)
    seed_into(
        destination,
        "package-python-nanobind",
        {
            "package_name": "acme-ext",
            "package_description": "acme-ext: a compiled extension.",
            "namespace_package": "acme",
            "author_name": answers["author_name"],
            "author_email": answers["author_email"],
            "copyright_year": answers["copyright_year"],
            "project_name": "acme",
        },
    )
    return _package(destination, "acme-ext")


# The guard and the refusals first.


def test_a_pure_wheel_from_a_nanobind_build_refuses(tmp_path: Path) -> None:
    package = _package(tmp_path / "packages" / "ext", "acme-ext")
    dist = package.directory / "dist"
    dist.mkdir(parents=True)
    with pytest.raises(_FAILURES, match="no wheel"):
        _build.assert_platform_tagged(package, dist)
    (dist / "acme_ext-0.0.1-py3-none-any.whl").touch()
    with pytest.raises(_FAILURES, match="platform tag is the proof"):
        _build.assert_platform_tagged(package, dist)
    (dist / "acme_ext-0.0.1-py3-none-any.whl").unlink()
    (dist / "acme_ext-0.0.1-cp314-cp314-macosx_11_0_arm64.whl").touch()
    _build.assert_platform_tagged(package, dist)


def test_the_seed_chain_runs_from_python_s_and_no_package_names_its_record() -> None:
    assert template_chain("package-python-nanobind") == (
        "package-base",
        "package-python",
        "package-python-nanobind",
    )
    # The record holds the seed chain alone: it builds nothing, and no
    # contract's kind names it.
    record = kind_for("python-nanobind")
    assert (record.parent, record.abstract, record.backend) == ("python", True, None)
    assert record.stands_for == ()
    assert "python-nanobind" not in kind_names()


def test_a_package_listing_nanobind_holds_python_cpp_and_conan(
    python_steps: object,
) -> None:
    """Python's extension, cpp and conan, which nanobind requires.

    A nanobind package is a python distribution whose extension module
    is C++ (cpp), it consumes conan's packages without being a conan
    package (conan, for the provider that resolves them), and nanobind
    builds its wheels. The tools the build runs are nanobind's.
    """
    from livery.workshop._extensions import composed_set, declaration
    from livery.workshop._steps import judges_extensions, steps_by_name, tools_for

    held = composed_set(["nanobind"])
    assert held == ("nanobind", "python", "cpp", "conan")
    # A step naming python judges the package: its tool joins the profile.
    assert {"fake"} <= {tool for tool, _ in tools_for(held)}
    phases = {r.phase for r in steps_by_name().values() if judges_extensions(r, held)}
    assert {"test", "typecheck"} <= phases
    declared = declaration("nanobind")
    assert declared is not None
    assert declared.tools == ("cmake", "ninja", "conan", "cmake_conan")


def test_the_one_wheel_skip_follows_the_hosts_libc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = "livery.extensions.nanobind._build"
    monkeypatch.setattr(f"{module}._musl_loader_present", lambda: False)
    monkeypatch.setattr(
        "sysconfig.get_config_var", lambda name: "aarch64-unknown-linux-musl"
    )
    assert _build.host_is_musl()
    monkeypatch.setattr(
        "sysconfig.get_config_var", lambda name: "aarch64-unknown-linux-gnu"
    )
    assert not _build.host_is_musl()
    # The loader is the second signal, for a musl-built python elsewhere.
    monkeypatch.setattr(f"{module}._musl_loader_present", lambda: True)
    assert _build.host_is_musl()


# The chain's seeds: the parent's first, the leaf's over them.


def test_the_chain_seeds_parent_files_under_the_leaf(tmp_path: Path) -> None:
    package = _render_chain(tmp_path)
    directory = package.directory
    # The parent's files survive beneath the leaf's.
    assert (directory / "LICENSE").is_file()
    assert (directory / "src" / "acme" / "ext" / "py.typed").is_file()
    # The leaf's build files land over them.
    assert (directory / "CMakeLists.txt").is_file()
    assert (directory / "src" / "acme" / "ext" / "_native.cpp").is_file()
    assert (directory / "src" / "acme" / "ext" / "_native.pyi").is_file()
    pyproject = (directory / "pyproject.toml").read_text()
    assert 'build-backend = "scikit_build_core.build"' in pyproject
    assert 'version = "0.0.0"' in pyproject
    # The build is keyed on the commit, so a fresh checkout at the same
    # commit reuses it instead of rebuilding on new modification times.
    assert "cache-keys = [{ git = { commit = true } }]" in pyproject
    # The contract is of the python kind and lists nanobind.
    contract = (directory / "workshop.toml").read_text()
    assert 'kind = "python"' in contract
    assert 'extensions = ["nanobind"]' in contract
    # The leaf's __init__ re-exports the compiled surface.
    init = (directory / "src" / "acme" / "ext" / "__init__.py").read_text()
    assert "native_hello" in init


# The armed leg: cibuildwheel builds, the tag is native, the
# isolated leg imports the compiled module.


def _render_library(tmp_path: Path) -> Package:
    """A rendered cpp-conan library beside the extension, named acme-geometry."""
    destination = tmp_path / "packages" / "geometry"
    answers = project_facts(ROOT)
    seed_into(
        destination,
        "package-cpp-conan",
        {
            "package_name": "acme-geometry",
            "package_description": "acme-geometry: a native library.",
            "namespace_package": "acme",
            "author_name": answers["author_name"],
            "author_email": answers["author_email"],
            "copyright_year": answers["copyright_year"],
            "project_name": "acme",
        },
    )
    return _package(
        destination, "acme-geometry", kind="cpp-conan", extensions=("cmake", "conan")
    )


def _consume_the_library(package: Package) -> None:
    """Make the rendered extension call the library beside it, at HEAD.

    The template alone consumes one third-party package; the fixture
    adds the sibling: a requirement at the floor, a `find_package`, a
    second exported function, and a test the isolated leg runs.
    """
    recipe = package.directory / "conanfile.py"
    recipe.write_text(
        recipe.read_text().replace(
            'requires = ("fmt/[>=11.0]",)',
            'requires = ("fmt/[>=11.0]", "acme-geometry/[>=0.0.0]")',
        )
    )
    cmake = package.directory / "CMakeLists.txt"
    cmake.write_text(
        cmake.read_text()
        .replace(
            "find_package(fmt REQUIRED)",
            "find_package(fmt REQUIRED)\nfind_package(acme-geometry REQUIRED)",
        )
        .replace(
            "target_link_libraries(_native PRIVATE fmt::fmt)",
            "target_link_libraries(_native PRIVATE fmt::fmt"
            " acme-geometry::acme-geometry)",
        )
    )
    native = package.directory / "src" / "acme" / "ext" / "_native.cpp"
    native.write_text(
        native.read_text()
        .replace(
            "#include <fmt/format.h>",
            "#include <fmt/format.h>\n#include <geometry.hpp>",
        )
        .replace(
            "NB_MODULE(_native, m) {",
            "NB_MODULE(_native, m) {\n"
            '    m.def("library_version",'
            " []() { return std::string(geometry::version()); });",
        )
    )
    tests = package.directory / "tests" / "test_ext_package.py"
    tests.write_text(
        tests.read_text()
        + "\n\ndef test_the_library_beside_it_is_linked_at_head() -> None:\n"
        "    from acme.ext._native import library_version\n\n"
        '    assert library_version() == "0.0.0"\n'
    )


# The build is the suite's largest cost by far (77 s on a linux leg),
# and it is the only proof that the native kinds build on every
# platform: the merge point's legs pay it on all three after a merge,
# the nightly point once more on the first, and a pull request's legs
# not at all.
@pytest.mark.only_at("merge", "nightly")
def test_the_wheel_is_platform_tagged_and_imports(
    build_rig: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The extension links one third-party and one first-party symbol.

    fmt comes from Conan Center; the library beside the extension
    resolves from its source at HEAD through the workspace's
    conanws.yml; the isolated leg calls both. The conan home is the
    test's own, so the packages it builds never reach the machine's
    cache, and nothing is registered in it.
    """
    from livery.extensions.conan import _package as _conan
    from livery.workshop._backends import _cpp_conan

    monkeypatch.setenv("CONAN_HOME", str(tmp_path / "conan-home"))
    _render_library(tmp_path)
    package = _render_chain(tmp_path)
    _consume_the_library(package)
    workspace = _cpp_conan.workspace_file(tmp_path)
    (tmp_path / _cpp_conan.WORKSPACE_FILE).write_text(workspace)
    dist = _build.build_dist(package, tmp_path)
    wheels = sorted(dist.glob("*.whl"))
    assert wheels, "cibuildwheel produced no wheel"
    assert all("none-any" not in wheel.name for wheel in wheels)
    assert list(dist.glob("*.tar.gz")), "the sdist is missing"
    # The isolated leg installs the wheel into a fresh venv and runs
    # the package's tests there; the test imports the compiled module.
    resolved = _python.run_isolated_test(package, tmp_path)
    assert "acme-ext" in resolved
    # The workspace file alone resolved the library: the home holds no
    # registration, asked from outside the workspace.
    listed = _conan._conan(tmp_path.parent, "editable", "list")  # pyright: ignore[reportPrivateUsage]
    assert "acme-geometry" not in listed.stdout + listed.stderr


def test_the_conan_workspace_names_each_conan_member_by_its_path_alone(
    tmp_path: Path,
) -> None:
    from livery.workshop._backends import _cpp_conan

    # An extension consumes conan and its recipe names no reference: no
    # member, so the file lists no package.
    (tmp_path / "packages").mkdir()
    _render_chain(tmp_path)
    assert _cpp_conan.workspace_file(tmp_path).endswith("packages: []\n")
    # The library beside it is a member. Its entry is its path: conan
    # reads the reference from the recipe, so a release's version stamp
    # never leaves the file behind.
    _render_library(tmp_path)
    text = _cpp_conan.workspace_file(tmp_path)
    assert text.endswith("packages:\n  - path: packages/geometry\n")
    assert "ref:" not in text


@pytest.mark.skipif(
    sys.platform == "win32", reason="the build container is Linux's, in POSIX paths"
)
def test_the_build_container_finds_a_workspace_file_at_its_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._backends import _cpp_conan

    (tmp_path / "packages").mkdir()
    _render_chain(tmp_path)
    _render_library(tmp_path)
    # The refusal first: a root without the file, as in a release leg
    # that set it aside, gives the container none, so a sibling
    # resolves from the package the leg created.
    assert _container.container_workspace(tmp_path) is None
    (tmp_path / _cpp_conan.WORKSPACE_FILE).write_text(
        _cpp_conan.workspace_file(tmp_path)
    )
    written = _container.container_workspace(tmp_path)
    assert written is not None
    # The package builds in /project, outside the workspace. The copy
    # names each member by its absolute path, where the container
    # mounts the workspace.
    library = (tmp_path / "packages" / "geometry").as_posix()
    assert written.read_text().endswith(f"packages:\n  - path: {library}\n")
    if sys.platform.startswith("linux"):
        provider = tmp_path / "conan_provider.cmake"
        monkeypatch.setattr(
            _container,
            "conan_tools",
            lambda root: (str(provider), str(tmp_path / "bin"), ""),
        )
        # The container's own conan install is another test's; here the
        # faked directory holds no binary to ask.
        monkeypatch.setattr(_container, "conan_version", lambda root, conan: "2.0.0")
        env = _container.conan_environment(tmp_path)
        assert f"-v {written}:/project/conanws.yml:ro" in env["CIBW_CONTAINER_ENGINE"]


def _receipt(root: Path, tool: str, version: str, **fields: object) -> Path:
    """Write a receipt for *tool* as `fm sync` would; the file."""
    import json

    from livery.workshop._tools import receipts_dir

    directory = receipts_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{tool}.json"
    body: dict[str, object] = {
        "schema": 1,
        "tool": tool,
        "version": version,
        "host": "linux-x64",
        "kind": "download",
        "mode": "path",
        "deployment": "sha256:" + "0" * 64,
        "tool_dir": "",
        "paths": [],
        "env": {},
        "entry_points": [],
    }
    body.update(fields)
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def test_the_build_rig_skips_where_the_native_tools_are_not_here(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A host that cannot resolve the tools skips the armed tests, never fails them.

    The skip is the fallback the armed tests rely on, and it asks about the
    workspace they build in: a temporary directory with no receipts, where
    the entered environment is the only source. A host with neither answers
    nothing, and the tests say so instead of failing inside the kind.
    """
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    monkeypatch.delenv("CMAKE_CONAN_PROVIDER", raising=False)
    with pytest.raises(Skipped) as skipped:
        build_rig.__wrapped__(tmp_path)  # type: ignore[attr-defined]
    assert "is not here" in str(skipped.value) or "toolchain" in str(skipped.value)


@pytest.mark.skipif(
    sys.platform == "win32", reason="the container mounts are Linux's, in POSIX paths"
)
def test_the_container_mounts_every_store_its_paths_name() -> None:
    # The fallback first: one path is one mount.
    assert _container.container_mounts((Path("/w"),)) == "-v /w:/w"
    # The entered provider and conan live in another store than this
    # process's: each gets its own mount, and a path inside one already
    # mounted adds nothing.
    mounts = _container.container_mounts(
        (
            Path("/w"),
            Path("/isolated/toolroom"),
            Path("/w/conan-home"),
            Path("/runner/toolroom/tools/cmake_conan@0.19.0"),
            Path("/runner/toolroom/tools/conan@2.32.0/bin"),
        )
    )
    assert mounts.split(" -v ") == [
        "-v /w:/w",
        "/isolated/toolroom:/isolated/toolroom",
        "/runner/toolroom/tools/cmake_conan@0.19.0"
        ":/runner/toolroom/tools/cmake_conan@0.19.0",
        "/runner/toolroom/tools/conan@2.32.0/bin:/runner/toolroom/tools/conan@2.32.0/bin",
    ]
    # A later path that holds an earlier one replaces it.
    assert _container.container_mounts((Path("/s/a"), Path("/s"))) == "-v /s:/s"


def test_the_conan_environment_refuses_without_the_store_and_names_the_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.toolroom.store import Home
    from livery.workshop import _tools

    home = Home(tmp_path / "toolroom")
    monkeypatch.setattr(_tools, "store_home", lambda: home)
    monkeypatch.delenv("CMAKE_CONAN_PROVIDER", raising=False)
    # An empty PATH, because conan falls back to it: the refusal is about
    # a host that has neither a receipt nor a conan of its own, and this
    # machine's own conan would otherwise answer.
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    # No receipts at all: both are named.
    with pytest.raises(_FAILURES, match="cmake-conan provider and conan"):
        _container.conan_environment(tmp_path)
    # The provider's path is the record's own env entry, carried by
    # the receipt; nothing here rebuilds it from a file name.
    provider = home.tools / "cmake_conan@0.19.0" / "conan_provider.cmake"
    _receipt(
        tmp_path,
        "cmake_conan",
        "0.19.0",
        mode="none",
        tool_dir=str(provider.parent),
        env={"CMAKE_CONAN_PROVIDER": str(provider)},
    )
    with pytest.raises(_FAILURES, match="conan is not here"):
        _container.conan_environment(tmp_path)
    # A receipt in a mode that puts nothing on PATH is no answer either.
    _receipt(tmp_path, "conan", "2.32.0", mode="none", entry_points=["conan"])
    with pytest.raises(_FAILURES, match="conan is not here"):
        _container.conan_environment(tmp_path)
    # Conan on PATH and nowhere else is an answer: a workspace with no
    # receipts of its own is what a CI leg's tests build in, and there the
    # entered environment carries the store's tools.
    entered = tmp_path / "entered"
    entered.mkdir()
    (entered / "conan").write_text("#!/bin/sh\nexit 0\n")
    (entered / "conan").chmod(0o755)
    monkeypatch.setenv("PATH", str(entered))
    if sys.platform != "win32":  # PATHEXT decides on Windows, and .sh is not on it
        assert _container.conan_tools(tmp_path)[1] == str(entered)
    monkeypatch.setenv("PATH", str(empty))
    conan_bin = home.tools / "conan@2.32.0" / "bin"
    _receipt(
        tmp_path,
        "conan",
        "2.32.0",
        tool_dir=str(conan_bin.parent),
        paths=[str(conan_bin)],
        entry_points=["conan"],
    )
    monkeypatch.setenv("CONAN_HOME", str(tmp_path / "conan-home"))
    env = _container.conan_environment(tmp_path)
    # The receipt's version wins over a newer directory in the store:
    # the lock, not the store's contents, says what this checkout uses.
    newer = home.tools / "cmake_conan@0.20.0" / "conan_provider.cmake"
    newer.parent.mkdir(parents=True)
    newer.write_text("# newer\n")
    assert env["CMAKE_CONAN_PROVIDER"] == str(provider)
    assert _container.conan_environment(tmp_path)["CMAKE_CONAN_PROVIDER"] == str(
        provider
    )
    assert env["CONAN_HOME"] == str(tmp_path / "conan-home")
    # Made before anything mounts it: a container's mount of a missing
    # directory creates it owned by root.
    assert (tmp_path / "conan-home").is_dir()
    if sys.platform.startswith("linux"):
        assert f"-v {tmp_path}:{tmp_path}" in env["CIBW_CONTAINER_ENGINE"]
        # The store lives inside the workspace here, so the workspace's
        # mount carries it and it gets none of its own.
        assert home.root.is_relative_to(tmp_path)
        assert f"-v {home.root}:" not in env["CIBW_CONTAINER_ENGINE"]
        # The container installs the receipt's conan for itself: the
        # store's is the host's frozen binary, which it cannot run.
        assert env["CIBW_BEFORE_ALL_LINUX"] == (
            _container.container_conan_install("2.32.0")
        )
        assert "conan==2.32.0" in env["CIBW_BEFORE_ALL_LINUX"]
        assert "CIBW_ENVIRONMENT_LINUX" not in env
        # No conan workspace here, so the container mounts none.
        assert "conanws.yml" not in env["CIBW_CONTAINER_ENGINE"]
    else:
        assert "CIBW_CONTAINER_ENGINE" not in env
    # An entered environment's provider is kept as it is.
    monkeypatch.setenv("CMAKE_CONAN_PROVIDER", "/entered/conan_provider.cmake")
    assert (
        _container.conan_environment(tmp_path)["CMAKE_CONAN_PROVIDER"]
        == "/entered/conan_provider.cmake"
    )


# The floor legs: a declared floor is a claim, and the leg proves it.


def _floor_workspace(tmp_path: Path) -> tuple[Path, Package]:
    """A workspace whose extension floors the library beside it at 0.1.0."""
    root = tmp_path / "ws"
    (root / "packages" / "geometry").mkdir(parents=True)
    (root / "workshop.toml").write_text("[workspace]\n")
    (root / "packages" / "geometry" / "workshop.toml").write_text(
        'kind = "cpp-conan"\nname = "acme-geometry"\n'
    )
    extension = root / "packages" / "ext"
    extension.mkdir(parents=True)
    (extension / "workshop.toml").write_text(
        'kind = "python"\nname = "acme-ext"\nextensions = ["nanobind"]\n'
        "[[depends]]\n"
        'path = "packages/geometry"\nkind = "build"\nfloor = "0.1.0"\n'
    )
    (extension / "pyproject.toml").write_text(
        '[project]\nname = "acme-ext"\nversion = "0.2.0"\n\n'
        "[tool.scikit-build.cmake.define]\n"
        'CONAN_INSTALL_ARGS = { env = "CONAN_INSTALL_ARGS",'
        ' default = "--build=missing;--build=editable" }\n'
    )
    from livery.workshop._packages import Edge

    package = Package(
        directory=extension,
        path="packages/ext",
        name="acme-ext",
        kind="python",
        depends=(Edge(path="packages/geometry", kind="build", floor="0.1.0"),),
        extensions=("nanobind",),
    )
    return root, package


def test_the_declared_conan_arguments_are_read_in_both_spellings(
    tmp_path: Path,
) -> None:
    _root, package = _floor_workspace(tmp_path)
    assert _build.conan_install_args(package) == ("--build=missing;--build=editable")
    (package.directory / "pyproject.toml").write_text(
        '[project]\nname = "acme-ext"\n\n'
        "[tool.scikit-build.cmake.define]\n"
        'CONAN_INSTALL_ARGS = "--build=missing"\n'
    )
    assert _build.conan_install_args(package) == "--build=missing"
    (package.directory / "pyproject.toml").write_text('[project]\nname = "acme-ext"\n')
    assert _build.conan_install_args(package) == (_build.DEFAULT_INSTALL_ARGS)


def test_a_floor_the_wave_itself_releases_is_the_build_that_already_ran(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root, package = _floor_workspace(tmp_path)

    def _never(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the leg must not build for a co-released floor")

    monkeypatch.setattr(_build, "build_wheels", _never)
    _ci.floor_legs(package, root, {"packages/geometry": "0.1.0"})
    printed = capsys.readouterr().out
    assert "the floor on acme-geometry is 0.1.0" in printed
    assert "already linked it" in printed


def test_the_floor_leg_pins_conan_to_the_floor_and_tests_that_wheel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root, package = _floor_workspace(tmp_path)
    restored: list[tuple[str, str]] = []
    built: list[tuple[Path, str, bool]] = []
    tested: list[Path] = []

    def _fake_restore(_root: Path, name: str, version: str, *, cwd: Path) -> None:
        del cwd
        restored.append((name, version))

    def _fake_build(
        _package: Package,
        _root: Path,
        dist: Path,
        *,
        epoch: int = 0,
        conan_install_args: str = "",
        one_host_wheel: bool = False,
    ) -> dict[str, str]:
        del epoch
        built.append((dist, conan_install_args, one_host_wheel))
        return {}

    def _fake_test(
        _package: Package, _root: Path, *, wheels_dir: Path | None = None, **_kw: object
    ) -> dict[str, str]:
        assert wheels_dir is not None
        tested.append(wheels_dir)
        return {}

    from livery.extensions.conan import _package as _conan

    monkeypatch.setattr(_conan, "restore_from_releases", _fake_restore)
    monkeypatch.setattr(_build, "build_wheels", _fake_build)
    monkeypatch.setattr(_python, "run_isolated_test", _fake_test)
    _ci.floor_legs(package, root, {"packages/geometry": "0.2.0"})
    assert restored == [("acme-geometry", "0.1.0")]
    dist, args, one_host = built[0]
    assert dist == package.directory / "build" / "floor"
    profile = package.directory / "build" / "floor-profile.txt"
    assert args == f"--build=missing;--build=editable;-pr:h;{profile.as_posix()}"
    assert profile.read_text() == (
        "[replace_requires]\nacme-geometry/*: acme-geometry/0.1.0\n"
    )
    assert one_host
    assert tested == [dist]
    assert "floor leg green against acme-geometry 0.1.0" in capsys.readouterr().out


# The build is a second cibuildwheel run and two conan creates; the
# merge point's legs and the nightly point pay it, a pull request's
# legs do not.
@pytest.mark.only_at("merge", "nightly")
def test_a_floor_whose_header_lacks_the_symbol_fails_the_leg(
    build_rig: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole route, end to end, on the version that must fail.

    The library is released twice: 0.1.0, whose header has no
    ``area``, and 0.2.0, which adds it. The extension calls ``area``
    and floors the library at 0.1.0, which is a lie. The leg
    restores 0.1.0 from its release, pins conan to it, and the
    compile says what is missing.
    """
    from livery.extensions.conan import _package as _conan
    from livery.forge.testing import FakeForge
    from livery.workshop._backends import _cpp_conan
    from livery.workshop._packages import Edge

    monkeypatch.setenv("CONAN_HOME", str(tmp_path / "conan-home"))
    library = _render_library(tmp_path)
    _cpp_conan.stamp_version(library).stamp("0.1.0")
    _cpp_conan.build(library, tmp_path)
    saved = _cpp_conan.save_cache(library, "0.1.0", library.directory / "dist")
    # 0.2.0 adds the symbol the extension calls.
    header = library.directory / "src" / "geometry.hpp"
    header.write_text(
        header.read_text().replace(
            "const char* version();",
            "const char* version();\n\ndouble area(double width, double height);",
        )
    )
    source = library.directory / "src" / "geometry.cpp"
    source.write_text(
        source.read_text().replace(
            'const char* version() { return "0.0.0"; }',
            'const char* version() { return "0.0.0"; }\n\n'
            "double area(double width, double height) { return width * height; }",
        )
    )
    _cpp_conan.stamp_version(library).stamp("0.2.0")
    _cpp_conan.build(library, tmp_path)
    # The floor's package leaves this machine's cache, so the leg has
    # to fetch it back from the release to build at all.
    _conan._conan(library.directory, "remove", "acme-geometry/0.1.0", "-c")

    extension = _render_chain(tmp_path)
    _consume_the_library(extension)
    native = extension.directory / "src" / "acme" / "ext" / "_native.cpp"
    native.write_text(
        native.read_text().replace(
            "NB_MODULE(_native, m) {",
            "NB_MODULE(_native, m) {\n"
            '    m.def("area", []() { return geometry::area(2.0, 3.0); });',
        )
    )
    package = Package(
        directory=extension.directory,
        path="packages/ext",
        name="acme-ext",
        kind="python",
        depends=(Edge(path="packages/geometry", kind="build", floor="0.1.0"),),
        extensions=("nanobind",),
    )

    fake = FakeForge()
    fake.create_repo("acme", "ws")
    repository = fake.repository("acme", "ws")
    tag = "packages/geometry/v0.1.0"
    fake.create_tag("acme", "ws", tag)
    repository.release.create(tag, name="acme-geometry 0.1.0")
    repository.release.upload_asset(tag, saved.name, saved.read_bytes())
    monkeypatch.setattr(
        "livery.workshop._forge_lane.this_repository", lambda _root: repository
    )

    with pytest.raises(_FAILURES) as caught:
        _ci.floor_legs(package, tmp_path, {"packages/geometry": "0.2.0"})
    message = str(caught.value)
    assert "area" in message, message


@pytest.mark.only_at("merge", "nightly")
def test_the_rendered_extension_configures_from_its_preset(
    build_rig: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The extension opens in an editor and configures, conan included.

    The preset names the generator, the build directory, the build
    type, the compile commands, the dependency provider and the venv
    the interpreter comes from, so `cmake --preset release` resolves
    the third-party requirement and configures with no argument of
    its own.
    """
    import subprocess

    monkeypatch.setenv("CONAN_HOME", str(tmp_path / "conan-home"))
    package = _render_chain(tmp_path)
    assert (package.directory / "CMakePresets.json").is_file()
    # A rendered workspace with an extension member carries nanobind in
    # its dev group, which is what the preset's interpreter answers
    # with; the fixture builds that venv for itself.
    venv = tmp_path / "venv"
    tools.uv("venv", str(venv))
    tools.uv.opts(env={**os.environ, "VIRTUAL_ENV": str(venv)})(
        "pip", "install", "nanobind>=2.7"
    )
    env = {
        **os.environ,
        **_container.conan_environment(tmp_path),
        "VIRTUAL_ENV": str(venv),
    }
    done = subprocess.run(
        ["cmake", "--preset", "release"],
        cwd=package.directory,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert done.returncode == 0, done.stdout[-4000:] + done.stderr[-2000:]
    assert (package.directory / "build" / "release" / "compile_commands.json").is_file()


def test_the_conan_version_comes_from_the_receipt_else_the_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No receipt and no binary: the refusal names fm sync, never the
    # spawn's own error.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    with pytest.raises(_FAILURES, match="no conan at"):
        _container.conan_version(tmp_path, str(bin_dir))
    # A binary that names nothing refuses the same way.
    conan = bin_dir / "conan"
    conan.write_text("#!/bin/sh\necho nothing here\n")
    conan.chmod(0o755)
    if sys.platform == "win32":
        return  # a shell script is no executable there; the receipt path is the same
    with pytest.raises(_FAILURES, match="names no version"):
        _container.conan_version(tmp_path, str(bin_dir))
    # No receipt: the binary's --version answers, as the provider reads it.
    conan.write_text("#!/bin/sh\necho 'Conan version 2.31.0'\n")
    assert _container.conan_version(tmp_path, str(bin_dir)) == "2.31.0"
    # A receipt names the lock's version, which wins over the binary.
    _receipt(tmp_path, "conan", "2.32.0", tool_dir=str(tmp_path), paths=[str(bin_dir)])
    assert _container.conan_version(tmp_path, str(bin_dir)) == "2.32.0"
    line = _container.container_conan_install("2.32.0")
    assert line.startswith("python -m venv /opt/workshop-conan && ")
    assert "pip install --quiet conan==2.32.0" in line
    # Every build runs the script in front of that install as its conan.
    assert line.endswith(
        _container.write_script(
            "/usr/local/bin/conan", _container.container_conan_wrapper()
        )
    )
    assert '/opt/workshop-conan/bin/conan "$@"' in (
        _container.container_conan_wrapper()
    )


@pytest.mark.skipif(
    sys.platform == "win32", reason="the script runs in a Linux build container"
)
def test_the_container_s_conan_gives_the_home_back_and_keeps_conan_s_exit_code(
    tmp_path: Path,
) -> None:
    import subprocess

    # Stand-ins: conan says it ran and fails, stat names the home's owner as
    # the container sees it, and chown says what it was asked to do.
    stand_ins = tmp_path / "bin"
    stand_ins.mkdir()
    said = tmp_path / "said"
    for name, body in (
        ("conan", f'echo "conan $*" >> "{said}"\nexit 3'),
        ("stat", "echo 1001:118"),
        ("chown", f'echo "chown $*" >> "{said}"'),
    ):
        (stand_ins / name).write_text(f"#!/bin/sh\n{body}\n")
        (stand_ins / name).chmod(0o755)
    wrapper = _container.container_conan_wrapper(str(stand_ins / "conan"))
    script = tmp_path / "conan"
    # The line the container runs writes the script exactly, executable.
    subprocess.run(
        ["sh", "-c", _container.write_script(str(script), wrapper)], check=True
    )
    assert script.read_text() == wrapper
    assert os.access(script, os.X_OK)
    path = os.pathsep.join((str(stand_ins), "/usr/bin", "/bin"))
    # The refusal first: with no home there is nothing to give back, and
    # conan's own exit code stands.
    ran = subprocess.run([str(script), "install", "."], env={"PATH": path}, check=False)
    assert ran.returncode == 3
    assert said.read_text().splitlines() == ["conan install ."]
    # With a home, every call ends by giving all of it to the home's owner.
    home = tmp_path / "conan-home"
    home.mkdir()
    said.unlink()
    ran = subprocess.run(
        [str(script), "install", "."],
        env={"PATH": path, "CONAN_HOME": str(home)},
        check=False,
    )
    assert ran.returncode == 3
    assert said.read_text().splitlines() == [
        "conan install .",
        f"chown -R 1001:118 {home}",
    ]


def test_the_workspace_file_lists_the_members_with_a_recipe_alone(
    tmp_path: Path,
) -> None:
    # A consumer first: its recipe names requirements and no reference,
    # so it is left out, since conan cannot deduce one for it; a member
    # with no recipe at all is left out the same way.
    from livery.extensions.conan import _package as _conan
    from livery.workshop._packages import discover_packages

    def member(name: str, *, recipe: bool) -> Package:
        """A member whose set holds conan: a library, or a package listing nanobind."""
        directory = tmp_path / "packages" / name
        directory.mkdir(parents=True)
        held = (
            'kind = "cpp-conan"\n'
            if recipe
            else 'kind = "python"\nextensions = ["nanobind"]\n'
        )
        (directory / "workshop.toml").write_text(f'{held}name = "{name}"\n')
        if not recipe:
            (directory / "pyproject.toml").write_text(f'[project]\nname = "{name}"\n')
        text = (
            f'name = "{name}"\nversion = "0.1.0"\n'
            if recipe
            else 'requires = ("fmt/[>=11.0]",)\n'
        )
        (directory / "conanfile.py").write_text(text)
        (found,) = [p for p in discover_packages(tmp_path) if p.name == name]
        return found

    consumer = member("ext", recipe=False)
    assert not _conan.names_a_reference(consumer)
    # Spelled as an empty list: conan's loader reads a bare key as
    # nothing and fails on it.
    assert _conan.workspace_file(tmp_path).endswith("packages: []\n")
    (consumer.directory / "conanfile.py").unlink()
    assert not _conan.names_a_reference(consumer)
    member("geometry", recipe=True)
    text = _conan.workspace_file(tmp_path)
    assert text.endswith("packages:\n  - path: packages/geometry\n")
    placed = _conan.workspace_file(tmp_path, absolute=True)
    assert placed.endswith(f"  - path: {(tmp_path / 'packages/geometry').as_posix()}\n")
