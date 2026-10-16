"""The cpp kind in the gate: what a C++ package's files are, and what judges them.

The package lists cmake and conan, a distribution of its own,
`livery-extensions-conan`, which requires cpp; a test that reads cpp's
answers through conan's requirement skips while conan is not
installed. conan's own suite covers its profile, its build and its
template, and the cmake extension's suite covers the gate build.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from livery.workshop._packages import Package
from livery.workshop._phases import PhaseContext
from livery.workshop._steps import judges, step_for
from workshop_cmake_declared import cmake_steps_fixture  # noqa: F401
from workshop_installed import skip_unless_installed
from workshop_python_steps import python_steps_fixture  # noqa: F401


def _package(directory: Path, name: str, *extensions: str) -> Package:
    return Package(
        directory=directory,
        path=f"packages/{directory.name}",
        name=name,
        depends=(),
        extensions=extensions,
    )


def test_the_cpp_kind_classifies_tests_support_source_and_configuration(
    tmp_path: Path,
) -> None:
    from livery.workshop._categories import (
        CONFIGURATION,
        SOURCE,
        TEST,
        TEST_SUPPORT,
        category_of,
    )

    skip_unless_installed("conan")
    package = _package(
        tmp_path / "packages" / "native", "acme-native", "cmake", "conan"
    )

    def category(path: str) -> str:
        return category_of(package, path).name

    assert category("tests/test_native.cpp") == TEST
    assert category("tests/helpers.hpp") == TEST_SUPPORT
    assert category("src/native.cpp") == SOURCE
    assert category("include/acme/native.hpp") == SOURCE
    assert category("CMakeLists.txt") == CONFIGURATION
    assert category("conanfile.py") == CONFIGURATION


def test_python_steps_skip_a_native_member_by_name(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    python_steps: object,
    cmake_steps: object,
) -> None:
    from livery.workshop._steps import judged_by

    py = _package(tmp_path / "packages" / "member", "acme-member", "python")
    native = _package(tmp_path / "packages" / "native", "acme-native", "cmake", "conan")
    for name in ("typecheck.fake", "test.fake"):
        assert judged_by(step_for(name), (py, native)) == (py,)
        out = capsys.readouterr().out
        assert f"{name}: packages/native skips (not a python package)" in out
    # A python formatter and linter judge both: the conanfile is python.
    # ctest judges the native member alone.
    for name in ("format.fake", "lint.fake"):
        assert judged_by(step_for(name), (py, native)) == (py, native)
        assert "skips" not in capsys.readouterr().out
    assert judged_by(step_for("test.ctest"), (py, native), quiet=True) == (native,)


# The native kind's steps, in the order the walk runs them.
NATIVE_CHECKS = (
    "build.configure",
    "build.compile",
    "test.ctest",
)


def test_a_pure_python_workspace_gate_is_unchanged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], cmake_steps: object
) -> None:
    py = _package(tmp_path / "packages" / "member", "acme-member", "python")
    from livery.workshop._steps import judged_by, judges_extensions, steps_by_name

    for record in steps_by_name().values():
        if judges_extensions(record, ("python",)):
            assert judged_by(record, (py,)) == (py,)
    # No package step judges a python package: the walk schedules
    # none of the native records for a workspace of python packages.
    ctx = PhaseContext(root=tmp_path, packages=(py,))
    assert not set(NATIVE_CHECKS) & set(judges(ctx))
    assert capsys.readouterr().out == ""


def test_a_package_listing_cmake_and_conan_is_categorised_by_cpp_s_table(
    tmp_path: Path,
) -> None:
    """Conan requires cpp, so a package listing cmake and conan reads cpp's table."""
    from livery.workshop._categories import category_of

    skip_unless_installed("conan")
    listing = _package(tmp_path / "packages" / "lib", "acme-lib", "cmake", "conan")
    expected = {
        "tests/test_geometry.cpp": "test",
        "tests/geometry_test.c": "test",
        "tests/CMakeLists.txt": "test-support",
        "src/geometry.cpp": "source",
        "include/acme/geometry.h": "source",
        "conanfile.py": "configuration",
        "docs/index.md": "prose",
    }
    found = {path: category_of(listing, path).name for path in expected}
    assert found == expected
    assert category_of(listing, "src/geometry.cpp").supplier == "cpp"


def test_the_project_render_wires_only_python_members(tmp_path: Path) -> None:
    destination = tmp_path / "scratch"
    # The members are what discovery finds: a python one and a cpp one.
    for member, contract, manifest in (
        ("alpha", 'extensions = ["python"]\nname = "acme-alpha"\n', "pyproject.toml"),
        (
            "native",
            'extensions = ["cmake", "conan"]\nname = "acme-native"\n',
            "conanfile.py",
        ),
    ):
        (destination / "packages" / member).mkdir(parents=True)
        (destination / "packages" / member / "workshop.toml").write_text(contract)
        (destination / "packages" / member / manifest).write_text("")
    from workshop_composed import compose_into

    pyproject = (compose_into(destination) / "pyproject.toml").read_text()
    assert '"packages/alpha"' in pyproject
    assert 'members = ["packages/alpha"]' in pyproject
    assert "acme-native" not in pyproject
    assert '"packages/native/src"' not in pyproject
