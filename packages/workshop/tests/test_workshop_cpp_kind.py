"""A C++ package in the gate: which steps judge it, and what the project render wires.

The package lists cmake and conan, which ship in distributions of their
own, as cpp does. cpp's own suite covers the categories it gives a
package's paths, conan's its profile, its build and its template, and
the cmake extension's suite the gate build.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from livery.workshop._packages import Package
from livery.workshop._phases import PhaseContext
from livery.workshop._steps import judges, step_for
from workshop_cmake_declared import cmake_steps_fixture  # noqa: F401
from workshop_python_steps import python_steps_fixture  # noqa: F401


def _package(directory: Path, name: str, *extensions: str) -> Package:
    return Package(
        directory=directory,
        path=f"packages/{directory.name}",
        name=name,
        depends=(),
        extensions=extensions,
    )


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
