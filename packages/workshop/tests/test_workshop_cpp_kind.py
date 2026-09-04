"""The cpp-conan kind: refusals and skips first, then the armed build."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from livery.workshop._backends import _cpp_conan
from livery.workshop._kinds import (
    CiContract,
    KindRecord,
    is_python_kind,
    kind_for,
    managed_files,
    record_for_template,
    register_kind,
    template_chain,
)
from livery.workshop._packages import Package, discover_packages
from livery.workshop._quality import gated, run_kind_checks
from livery.workshop._templates import read_answers, render

_FAILURES = (BaseException,)

ROOT = Path(__file__).resolve().parents[3]
TEMPLATES = ROOT / "packages/workshop/src/livery/workshop/templates"

#: The armed leg needs the host toolchain; a machine without it skips
#: naming what is missing instead of failing mid-configure.
_TOOLCHAIN = ("cmake", "ninja", "cc", "c++")
_MISSING_TOOLS = tuple(tool for tool in _TOOLCHAIN if shutil.which(tool) is None)
needs_toolchain = pytest.mark.skipif(
    bool(_MISSING_TOOLS),
    reason=f"host toolchain incomplete: {', '.join(_MISSING_TOOLS)} missing",
)


@pytest.fixture
def restored_registry():
    from livery.workshop import _kinds

    before = dict(_kinds._KINDS)
    yield
    _kinds._KINDS.clear()
    _kinds._KINDS.update(before)


def _package(directory: Path, name: str, type_name: str) -> Package:
    return Package(
        directory=directory,
        path=f"packages/{directory.name}",
        name=name,
        type=type_name,
        depends=(),
    )


def _render_cpp(tmp_path: Path) -> Package:
    """A rendered cpp-conan package, straight from the template."""
    destination = tmp_path / "packages" / "native"
    answers = read_answers(ROOT / ".copier-answers.yml")
    render(
        str(TEMPLATES),
        destination,
        {
            "kind": "package-cpp-conan",
            "package_name": "acme-native",
            "package_description": "acme-native: a native library.",
            "namespace_package": "acme",
            "author_name": answers["author_name"],
            "author_email": answers["author_email"],
            "copyright_year": answers["copyright_year"],
            "project_name": "acme",
        },
    )
    return _package(destination, "acme-native", "cpp-conan")


# The refusals and the skips first.


def test_build_refuses_without_conan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = tmp_path / "empty-path"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    package = _package(tmp_path / "packages" / "native", "acme-native", "cpp-conan")
    package.directory.mkdir(parents=True)
    with pytest.raises(_FAILURES, match="conan is not on"):
        _cpp_conan.build(package, tmp_path)


@needs_toolchain
def test_a_red_ctest_is_a_refusal(tmp_path: Path) -> None:
    package = _render_cpp(tmp_path)
    test_file = package.directory / "tests" / "test_native.cpp"
    broken = test_file.read_text().replace("return 0;", "return 1;")
    test_file.write_text(broken)
    with pytest.raises(_FAILURES, match="ctest failed"):
        _cpp_conan.check(package, tmp_path)


def test_discovery_requires_pyproject_only_of_python_kinds(tmp_path: Path) -> None:
    packages_dir = tmp_path / "packages"
    (packages_dir / "native").mkdir(parents=True)
    (packages_dir / "native" / "workshop.toml").write_text(
        'type = "cpp-conan"\nname = "acme-native"\n'
    )
    found = discover_packages(tmp_path)
    assert [package.name for package in found] == ["acme-native"]
    # A python member without its pyproject still refuses.
    (packages_dir / "member").mkdir(parents=True)
    (packages_dir / "member" / "workshop.toml").write_text(
        'type = "python"\nname = "acme-member"\n'
    )
    with pytest.raises(ValueError, match=r"member: no pyproject\.toml"):
        discover_packages(tmp_path)


def test_python_verbs_skip_by_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    py = _package(tmp_path / "packages" / "member", "acme-member", "python")
    native = _package(tmp_path / "packages" / "native", "acme-native", "cpp-conan")
    for verb in ("typecheck", "typecomplete", "test"):
        assert gated((py, native), verb) == (py,)
        out = capsys.readouterr().out
        assert f"{verb}: packages/native skips (cpp-conan kind)" in out
    # format and lint stay: the conanfile is python and ruff gates it.
    for verb in ("format", "lint"):
        assert gated((py, native), verb) == (py, native)
        assert "skips" not in capsys.readouterr().out


def test_a_pure_python_workspace_gate_is_unchanged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    py = _package(tmp_path / "packages" / "member", "acme-member", "python")
    for verb in CiContract().check_verbs:
        assert gated((py,), verb) == (py,)
    run_kind_checks((py,), tmp_path)
    assert capsys.readouterr().out == ""


def test_kind_checks_announce_and_dispatch(
    restored_registry, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    checked: list[str] = []

    class _Spy:
        def build(self, package: Package, root: Path, *, epoch: int = 0) -> Path:
            return package.directory

        def check(self, package: Package, root: Path) -> None:
            checked.append(package.name)

    record = kind_for("cpp-conan")
    register_kind(
        KindRecord(
            name="cpp-conan",
            backend=_Spy(),
            template=record.template,
            tools=record.tools,
            host_tools=record.host_tools,
            managed=record.managed,
            ci=record.ci,
        )
    )
    native = _package(tmp_path / "packages" / "native", "acme-native", "cpp-conan")
    run_kind_checks((native,), tmp_path)
    out = capsys.readouterr().out
    assert "packages/native (cpp-conan): configure, build, ctest run" in out
    assert "typecheck, typecomplete, test skip" in out
    assert checked == ["acme-native"]


def test_host_tools_are_named_when_missing(restored_registry, tmp_path: Path) -> None:
    from livery.workshop._env_tasks import missing_host_tools

    (tmp_path / "packages" / "member").mkdir(parents=True)
    (tmp_path / "packages" / "member" / "workshop.toml").write_text(
        'type = "python"\nname = "acme-member"\n'
    )
    (tmp_path / "packages" / "member" / "pyproject.toml").write_text(
        '[project]\nname = "acme-member"\n'
    )
    assert missing_host_tools(tmp_path) == ()

    class _Idle:
        def build(self, package: Package, root: Path, *, epoch: int = 0) -> Path:
            return package.directory

        def check(self, package: Package, root: Path) -> None:
            return None

    register_kind(
        KindRecord(
            name="cpp-fake",
            backend=_Idle(),
            host_tools=("surely-absent-compiler",),
        )
    )
    (tmp_path / "packages" / "native").mkdir(parents=True)
    (tmp_path / "packages" / "native" / "workshop.toml").write_text(
        'type = "cpp-fake"\nname = "acme-native"\n'
    )
    assert missing_host_tools(tmp_path) == ("surely-absent-compiler",)


# The registry facts.


def test_the_kind_registers_alone_in_the_chain() -> None:
    assert template_chain("package-cpp-conan") == ("package-cpp-conan",)
    assert managed_files("cpp-conan") == ("cliff.toml",)
    assert not is_python_kind("cpp-conan")
    assert is_python_kind("python")
    record = record_for_template("package-cpp-conan")
    assert record is not None and record.name == "cpp-conan"
    assert record_for_template("package-python-layer") is None
    assert kind_for("cpp-conan").tools == ("cmake", "conan", "ninja")


def test_the_project_render_wires_only_python_members(tmp_path: Path) -> None:
    destination = tmp_path / "scratch"
    answers = dict(read_answers(ROOT / ".copier-answers.yml"))
    answers["packages"] = [
        {"dir": "alpha", "name": "acme-alpha", "dev": "acme-alpha"},
        {"dir": "native", "name": "acme-native", "kind": "cpp-conan"},
    ]
    render(str(TEMPLATES), destination, {**answers, "kind": "project"})
    pyproject = (destination / "pyproject.toml").read_text()
    assert '"packages/alpha"' in pyproject
    assert 'members = ["packages/alpha"]' in pyproject
    assert "acme-native" not in pyproject
    assert 'exclude = ["packages/native"]' in pyproject
    assert '"packages/native/src"' not in pyproject


# The armed leg: the fixture builds and its ctest passes.


@needs_toolchain
def test_the_rendered_package_builds_and_its_ctest_passes(tmp_path: Path) -> None:
    package = _render_cpp(tmp_path)
    assert (package.directory / "conanfile.py").is_file()
    assert (package.directory / "CMakeLists.txt").is_file()
    assert (package.directory / "src" / "native.cpp").is_file()
    _cpp_conan.check(package, tmp_path)
    assert (package.directory / _cpp_conan.GATE_BUILD_DIR).is_dir()
