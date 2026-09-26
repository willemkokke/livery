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


# The build is the suite's largest cost by far (77 s on a linux leg);
# the nightly point pays it, a pull request's legs do not.
@pytest.mark.only_at("nightly")
@needs_build_rig
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
