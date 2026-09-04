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
        "package-python",
        "package-python-nanobind",
    )
    assert is_python_kind("python-nanobind")
    # The managed union is the parent's: the leaf adds build files
    # the package owns, not rendered-managed ones.
    assert managed_files("python-nanobind") == ("cliff.toml",)
    assert kind_tools({"python-nanobind"}) == ("cmake", "ninja")
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
    assert 'version = "0.0.1"' in pyproject
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


@needs_build_rig
def test_the_wheel_is_platform_tagged_and_imports(tmp_path: Path) -> None:
    package = _render_chain(tmp_path)
    dist = _python_nanobind.build(package, tmp_path)
    wheels = sorted(dist.glob("*.whl"))
    assert wheels, "cibuildwheel produced no wheel"
    assert all("none-any" not in wheel.name for wheel in wheels)
    assert list(dist.glob("*.tar.gz")), "the sdist is missing"
    # The isolated leg installs the wheel into a fresh venv and runs
    # the package's tests there; the test imports the compiled module.
    resolved = _python.run_isolated_test(package, tmp_path)
    assert "acme-ext" in resolved
