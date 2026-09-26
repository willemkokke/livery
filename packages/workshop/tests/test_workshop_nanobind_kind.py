"""The python-nanobind kind: the guard and chain first, then the armed build."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from livery.workshop._backends import _python, _python_nanobind
from livery.workshop._kinds import (
    is_python_kind,
    kind_for,
    kind_tools,
    managed_files,
    template_chain,
)
from livery.workshop._packages import Package
from livery.workshop._templates import read_answers, render

_FAILURES = (BaseException,)

ROOT = Path(__file__).resolve().parents[3]
TEMPLATES = ROOT / "packages/workshop/src/livery/workshop/templates"


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


needs_build_rig = pytest.mark.skipif(bool(_toolchain_gap()), reason=_toolchain_gap())


def _package(directory: Path, name: str) -> Package:
    return Package(
        directory=directory,
        path=f"packages/{directory.name}",
        name=name,
        type="python-nanobind",
        depends=(),
    )


def _render_chain(tmp_path: Path) -> Package:
    """A rendered python-nanobind package, parent then leaf."""
    destination = tmp_path / "packages" / "ext"
    answers = read_answers(ROOT / ".copier-answers.yml")
    for kind in template_chain("package-python-nanobind"):
        render(
            str(TEMPLATES),
            destination,
            {
                "kind": kind,
                "package_dir": "ext",
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


def test_a_pure_wheel_from_the_native_kind_refuses(tmp_path: Path) -> None:
    package = _package(tmp_path / "packages" / "ext", "acme-ext")
    dist = package.directory / "dist"
    dist.mkdir(parents=True)
    with pytest.raises(_FAILURES, match="no wheel"):
        _python_nanobind.assert_platform_tagged(package, dist)
    (dist / "acme_ext-0.0.1-py3-none-any.whl").touch()
    with pytest.raises(_FAILURES, match="platform tag is the proof"):
        _python_nanobind.assert_platform_tagged(package, dist)
    (dist / "acme_ext-0.0.1-py3-none-any.whl").unlink()
    (dist / "acme_ext-0.0.1-cp314-cp314-macosx_11_0_arm64.whl").touch()
    _python_nanobind.assert_platform_tagged(package, dist)


def test_the_kind_chains_from_python() -> None:
    assert template_chain("package-python-nanobind") == (
        "package-base",
        "package-python",
        "package-python-nanobind",
    )
    assert is_python_kind("python-nanobind")
    # The managed union is the parent's: the leaf adds build files
    # the package owns, not rendered-managed ones.
    assert managed_files("python-nanobind") == ("cliff.toml",)
    # The chain's union: the base's and python's tools beneath the kind's own four.
    assert kind_tools({"python-nanobind"}) == (
        "basedpyright",
        "cmake",
        "cmake_conan",
        "conan",
        "git_cliff",
        "mypy",
        "ninja",
        "pyrefly",
        "pytest",
        "ruff",
        "ty",
        "uv",
    )
    record = kind_for("python-nanobind")
    assert record.parent == "python"
    assert record.ci.check_verbs == (
        "format",
        "lint",
        "typecheck",
        "typecomplete",
        "test",
    )
    assert record.ci.kind_verbs == ()
    assert record.host_tools == ("cc", "c++")


# The chain render: the phase 1 deferred acceptance closes here.


def test_the_chain_renders_parent_files_under_the_leaf(tmp_path: Path) -> None:
    package = _render_chain(tmp_path)
    directory = package.directory
    # The parent's files survive beneath the leaf's.
    assert (directory / "cliff.toml").is_file()
    assert (directory / "LICENSE").is_file()
    assert (directory / "src" / "acme" / "ext" / "py.typed").is_file()
    # The leaf's build files land over them.
    assert (directory / "CMakeLists.txt").is_file()
    assert (directory / "src" / "acme" / "ext" / "_native.cpp").is_file()
    assert (directory / "src" / "acme" / "ext" / "_native.pyi").is_file()
    pyproject = (directory / "pyproject.toml").read_text()
    assert 'build-backend = "scikit_build_core.build"' in pyproject
    assert 'version = "0.0.0"' in pyproject
    # The contract and the receipt record the leaf kind.
    assert 'type = "python-nanobind"' in (directory / "workshop.toml").read_text()
    assert "package-python-nanobind" in (directory / ".copier-answers.yml").read_text()
    # The leaf's __init__ re-exports the compiled surface.
    init = (directory / "src" / "acme" / "ext" / "__init__.py").read_text()
    assert "native_hello" in init


def test_the_drift_loop_renders_the_chain(tmp_path: Path) -> None:
    from livery.workshop._templates import apply_packages, apply_project

    shutil.copytree(TEMPLATES, tmp_path / "templates")
    shutil.copy(ROOT / ".copier-answers.yml", tmp_path / ".copier-answers.yml")
    (tmp_path / "workshop.toml").write_text(
        "[workspace]\n"
        'layers = ["livery.workshop"]\n'
        'templates = "templates"\n'
        "\n"
        "[forge]\n"
        'kind = "github"\n'
        'owner = "owner"\n'
    )
    apply_project(tmp_path)
    package = tmp_path / "packages" / "ext"
    package.mkdir(parents=True)
    answers = read_answers(ROOT / "packages" / "workshop" / ".copier-answers.yml")
    answers["package_name"] = "livery-ext"
    answers["kind"] = "package-python-nanobind"
    (package / ".copier-answers.yml").write_text(
        "\n".join(f"{key}: {value!r}" for key, value in answers.items()) + "\n"
    )
    # cliff.toml is the parent's managed file: the leaf render alone
    # cannot produce it, so this forces the chain through the loop.
    assert "packages/ext/cliff.toml" in apply_packages(tmp_path)
    body = (package / "cliff.toml").read_text()
    assert 'include_paths = ["packages/ext/**"]' in body


# The armed leg: cibuildwheel builds, the tag is native, the
# isolated leg imports the compiled module.


def _render_library(tmp_path: Path) -> Package:
    """A rendered cpp-conan library beside the extension, named acme-geometry."""
    destination = tmp_path / "packages" / "geometry"
    answers = read_answers(ROOT / ".copier-answers.yml")
    render(
        str(TEMPLATES),
        destination,
        {
            "kind": "package-cpp-conan",
            "package_name": "acme-geometry",
            "package_description": "acme-geometry: a native library.",
            "namespace_package": "acme",
            "author_name": answers["author_name"],
            "author_email": answers["author_email"],
            "copyright_year": answers["copyright_year"],
            "project_name": "acme",
        },
    )
    return _package(destination, "acme-geometry")


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
@needs_build_rig
def test_the_wheel_is_platform_tagged_and_imports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The extension links one third-party and one first-party symbol.

    fmt comes from Conan Center; the library beside the extension is
    registered editable and built from HEAD; the isolated leg calls both.
    The conan home is the test's own, so the editable and the packages
    it builds never reach the machine's cache.
    """
    from livery.workshop._sync import conan_editables

    monkeypatch.setenv("CONAN_HOME", str(tmp_path / "conan-home"))
    _render_library(tmp_path)
    package = _render_chain(tmp_path)
    _consume_the_library(package)
    registered = conan_editables(tmp_path)
    assert registered == ["  conan editable: packages/geometry at HEAD"], registered
    dist = _python_nanobind.build(package, tmp_path)
    wheels = sorted(dist.glob("*.whl"))
    assert wheels, "cibuildwheel produced no wheel"
    assert all("none-any" not in wheel.name for wheel in wheels)
    assert list(dist.glob("*.tar.gz")), "the sdist is missing"
    # The isolated leg installs the wheel into a fresh venv and runs
    # the package's tests there; the test imports the compiled module.
    resolved = _python.run_isolated_test(package, tmp_path)
    assert "acme-ext" in resolved


def test_the_editable_step_says_when_conan_is_missing_and_does_nothing_without_a_member(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._sync import conan_editables

    # No cpp-conan member: nothing to register, nothing said.
    (tmp_path / "packages").mkdir()
    _render_chain(tmp_path)
    assert conan_editables(tmp_path) == []
    # A member, and no conan on PATH: the line names the store's way in.
    _render_library(tmp_path)
    monkeypatch.setattr("shutil.which", lambda _name: None)
    (line,) = conan_editables(tmp_path)
    assert "conan is not on PATH" in line and "sync" in line


def test_the_conan_environment_refuses_without_the_store_and_names_the_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.toolroom.store import Home
    from livery.workshop import _tools

    home = Home(tmp_path / "toolroom")
    monkeypatch.setattr(_tools, "store_home", lambda: home)
    monkeypatch.delenv("CMAKE_CONAN_PROVIDER", raising=False)
    monkeypatch.setattr("shutil.which", lambda _name: None)
    with pytest.raises(_FAILURES, match="cmake-conan provider is not materialised"):
        _python_nanobind.conan_environment(tmp_path)
    provider = home.tools / "cmake_conan@0.19.0" / "conan_provider.cmake"
    provider.parent.mkdir(parents=True)
    provider.write_text("# provider\n")
    with pytest.raises(_FAILURES, match="conan is not materialised"):
        _python_nanobind.conan_environment(tmp_path)
    conan = home.tools / "conan@2.32.0" / "bin" / "conan"
    conan.parent.mkdir(parents=True)
    conan.write_text("#!/bin/sh\n")
    older = home.tools / "cmake_conan@0.18.1" / "conan_provider.cmake"
    older.parent.mkdir(parents=True)
    older.write_text("# old\n")
    monkeypatch.setenv("CONAN_HOME", str(tmp_path / "conan-home"))
    env = _python_nanobind.conan_environment(tmp_path)
    # The newest install wins, and the caller's conan home is kept.
    assert env["CMAKE_CONAN_PROVIDER"] == str(provider)
    assert env["CONAN_HOME"] == str(tmp_path / "conan-home")
    if sys.platform.startswith("linux"):
        assert f"-v {tmp_path}:{tmp_path}" in env["CIBW_CONTAINER_ENGINE"]
        assert f"-v {home.root}:{home.root}" in env["CIBW_CONTAINER_ENGINE"]
        assert str(conan.parent) in env["CIBW_ENVIRONMENT_LINUX"]
    else:
        assert "CIBW_CONTAINER_ENGINE" not in env
    # An entered environment's provider is kept as it is.
    monkeypatch.setenv("CMAKE_CONAN_PROVIDER", "/entered/conan_provider.cmake")
    assert (
        _python_nanobind.conan_environment(tmp_path)["CMAKE_CONAN_PROVIDER"]
        == "/entered/conan_provider.cmake"
    )


# The floor legs: a declared floor is a claim, and the leg proves it.


def _floor_workspace(tmp_path: Path) -> tuple[Path, Package]:
    """A workspace whose extension floors the library beside it at 0.1.0."""
    root = tmp_path / "ws"
    (root / "packages" / "geometry").mkdir(parents=True)
    (root / "workshop.toml").write_text("[workspace]\n")
    (root / "packages" / "geometry" / "workshop.toml").write_text(
        'type = "cpp-conan"\nname = "acme-geometry"\n'
    )
    extension = root / "packages" / "ext"
    extension.mkdir(parents=True)
    (extension / "workshop.toml").write_text(
        'type = "python-nanobind"\nname = "acme-ext"\n'
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
        type="python-nanobind",
        depends=(Edge(path="packages/geometry", kind="build", floor="0.1.0"),),
    )
    return root, package


def test_the_declared_conan_arguments_are_read_in_both_spellings(
    tmp_path: Path,
) -> None:
    _root, package = _floor_workspace(tmp_path)
    assert _python_nanobind.conan_install_args(package) == (
        "--build=missing;--build=editable"
    )
    (package.directory / "pyproject.toml").write_text(
        '[project]\nname = "acme-ext"\n\n'
        "[tool.scikit-build.cmake.define]\n"
        'CONAN_INSTALL_ARGS = "--build=missing"\n'
    )
    assert _python_nanobind.conan_install_args(package) == "--build=missing"
    (package.directory / "pyproject.toml").write_text('[project]\nname = "acme-ext"\n')
    assert _python_nanobind.conan_install_args(package) == (
        _python_nanobind.DEFAULT_INSTALL_ARGS
    )


def test_a_floor_the_wave_itself_releases_is_the_build_that_already_ran(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root, package = _floor_workspace(tmp_path)

    def _never(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the leg must not build for a co-released floor")

    monkeypatch.setattr(_python_nanobind, "build_wheels", _never)
    _python_nanobind.floor_legs(package, root, {"packages/geometry": "0.1.0"})
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

    from livery.workshop._backends import _cpp_conan

    monkeypatch.setattr(_cpp_conan, "restore_from_releases", _fake_restore)
    monkeypatch.setattr(_python_nanobind, "build_wheels", _fake_build)
    monkeypatch.setattr(_python, "run_isolated_test", _fake_test)
    _python_nanobind.floor_legs(package, root, {"packages/geometry": "0.2.0"})
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
@needs_build_rig
def test_a_floor_whose_header_lacks_the_symbol_fails_the_leg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole route, end to end, on the version that must fail.

    The library is released twice: 0.1.0, whose header has no
    ``area``, and 0.2.0, which adds it. The extension calls ``area``
    and floors the library at 0.1.0, which is a lie. The leg
    restores 0.1.0 from its release, pins conan to it, and the
    compile says what is missing.
    """
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
    _cpp_conan._conan(library.directory, "remove", "acme-geometry/0.1.0", "-c")

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
        type="python-nanobind",
        depends=(Edge(path="packages/geometry", kind="build", floor="0.1.0"),),
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
        _python_nanobind.floor_legs(package, tmp_path, {"packages/geometry": "0.2.0"})
    message = str(caught.value)
    assert "area" in message, message
