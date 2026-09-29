"""The cpp-conan kind: refusals and skips first, then the armed build."""

from __future__ import annotations

import json
import os
import platform
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from livery.workshop._backends import _cpp_conan
from livery.workshop._checks import (
    GateContext,
    check_for,
    judges,
    register_check,
    run_check,
)
from livery.workshop._kinds import (
    CiContract,
    KindRecord,
    gated,
    is_python_kind,
    kind_for,
    managed_files,
    record_for_template,
    register_kind,
    template_chain,
)
from livery.workshop._packages import Neighbours, Package, discover_packages
from livery.workshop._registries import RegistryTarget
from livery.workshop._templates import read_answers, render

_FAILURES = (BaseException,)


class _FakeStamper:
    """A stamper that changes nothing; the fakes' version home."""

    def stamp(self, version: str) -> list[str]:
        return []


ROOT = Path(__file__).resolve().parents[3]
TEMPLATES = ROOT / "packages/workshop/src/livery/workshop/templates"

#: The armed leg needs the host toolchain; a machine without it skips
#: naming what is missing instead of failing mid-configure. Windows
#: builds with MSVC, whose leg is `needs_msvc`.
_TOOLCHAIN = ("cmake", "ninja", "cc", "c++")
_MISSING_TOOLS = tuple(tool for tool in _TOOLCHAIN if shutil.which(tool) is None)
needs_toolchain = pytest.mark.skipif(
    bool(_MISSING_TOOLS) or sys.platform == "win32",
    reason=f"host toolchain incomplete: {', '.join(_MISSING_TOOLS)} missing"
    if _MISSING_TOOLS
    else "the Windows leg builds with MSVC; needs_msvc covers it",
)

#: The MSVC leg: Windows with Visual Studio's installer to name an
#: installation, and the store's cmake and ninja on PATH.
_MISSING_MSVC = tuple(tool for tool in ("cmake", "ninja") if shutil.which(tool) is None)
needs_msvc = pytest.mark.skipif(
    sys.platform != "win32"
    or not _cpp_conan.vswhere_path().is_file()
    or bool(_MISSING_MSVC),
    reason="the MSVC leg needs Windows, Visual Studio's installer, cmake and ninja",
)


@pytest.fixture
def hermetic_toolchain(monkeypatch: pytest.MonkeyPatch) -> None:
    """The gate's tools run in this process's environment, whatever the host has."""
    monkeypatch.setattr(_cpp_conan, "toolchain_env", lambda: dict(os.environ))


@pytest.fixture
def fresh_toolchain() -> object:
    """The toolchain environment read afresh by this test, and by the next."""
    _cpp_conan._entered.cache_clear()
    yield None
    _cpp_conan._entered.cache_clear()


@pytest.fixture
def restored_registry():
    from livery.workshop import _checks, _kinds

    before = dict(_kinds._KINDS)
    checks = _checks.snapshot()
    yield
    _kinds._KINDS.clear()
    _kinds._KINDS.update(before)
    _checks.restore(checks)


def _package(directory: Path, name: str, kind_name: str) -> Package:
    return Package(
        directory=directory,
        path=f"packages/{directory.name}",
        name=name,
        kind=kind_name,
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
    # The native configs come from the check records, as they do at a
    # birth: the template ships none.
    from livery.workshop._templates import settle_fragment_files

    settle_fragment_files(
        destination, {"kind": "package-cpp-conan", "package_dir": "native"}
    )
    return _package(destination, "acme-native", "cpp-conan")


# The refusals and the skips first.


def test_build_refuses_without_conan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A handle spawns by name, so an undeployed tool is a sentence.

    The spawn raises OSError instead of answering with a failing
    result, and a traceback names nothing a person can act on.
    """
    empty = tmp_path / "empty-path"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    package = _package(tmp_path / "packages" / "native", "acme-native", "cpp-conan")
    package.directory.mkdir(parents=True)
    with pytest.raises(_FAILURES, match="conan is not on PATH"):
        _cpp_conan.build(package, tmp_path)


@pytest.mark.usefixtures("hermetic_toolchain")
def test_a_selected_test_refuses_when_ctest_is_not_deployed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ctest rides the cmake record, and its absence reads the same."""
    empty = tmp_path / "empty-path"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    package = _package(tmp_path / "packages" / "native", "acme-native", "cpp-conan")
    package.directory.mkdir(parents=True)
    with pytest.raises(_FAILURES, match="ctest is not on PATH"):
        _cpp_conan.test(package, tmp_path, selection=("tests/test_native.cpp",))


def test_the_cpp_kind_classifies_tests_support_source_and_configuration(
    tmp_path: Path,
) -> None:
    from livery.workshop._categories import category_of
    from livery.workshop._kinds import CONFIGURATION, SOURCE, TEST, TEST_SUPPORT

    package = _package(tmp_path / "packages" / "native", "acme-native", "cpp-conan")

    def category(path: str) -> str:
        return category_of(package, path).name

    assert category("tests/test_native.cpp") == TEST
    assert category("tests/helpers.hpp") == TEST_SUPPORT
    assert category("src/native.cpp") == SOURCE
    assert category("include/acme/native.hpp") == SOURCE
    assert category("CMakeLists.txt") == CONFIGURATION
    assert category("conanfile.py") == CONFIGURATION
    # The kind's tests run on a build; python's run on source.
    assert kind_for("cpp-conan").tests_need_build
    assert not kind_for("python").tests_need_build


@needs_toolchain
def test_a_selection_no_ctest_answers_to_is_a_refusal(tmp_path: Path) -> None:
    package = _render_cpp(tmp_path)
    _cpp_conan.gate_build(package, tmp_path)
    with pytest.raises(_FAILURES, match="no ctest is named test_missing"):
        _cpp_conan.test(package, tmp_path, selection=("tests/test_missing.cpp",))


@needs_toolchain
def test_a_selected_test_runs_its_ctest_on_the_gate_build(tmp_path: Path) -> None:
    package = _render_cpp(tmp_path)
    _cpp_conan.gate_build(package, tmp_path)
    _cpp_conan.test(package, tmp_path, selection=("tests/test_native.cpp",))
    test_file = package.directory / "tests" / "test_native.cpp"
    test_file.write_text(test_file.read_text().replace("return 0;", "return 1;"))
    # The rebuild after the edit is what the reflex runs before the tests.
    _cpp_conan.gate_build(package, tmp_path)
    with pytest.raises(_FAILURES, match="ctest failed"):
        _cpp_conan.test(package, tmp_path, selection=("tests/test_native.cpp",))


@needs_toolchain
def test_a_red_ctest_is_a_refusal(tmp_path: Path) -> None:
    package = _render_cpp(tmp_path)
    test_file = package.directory / "tests" / "test_native.cpp"
    broken = test_file.read_text().replace("return 0;", "return 1;")
    test_file.write_text(broken)
    _cpp_conan.gate_build(package, tmp_path)
    with pytest.raises(_FAILURES, match="ctest failed"):
        _cpp_conan.test(package, tmp_path)


def test_the_rendered_cpp_member_passes_its_own_format_checks(tmp_path: Path) -> None:
    """No rendered python line is over the column limit; the sources are formatted."""
    import subprocess

    package = _render_cpp(tmp_path)
    over = [
        (path.name, line)
        for path in package.directory.rglob("*.py")
        for line in path.read_text("utf-8").splitlines()
        if len(line) > 88
    ]
    assert over == []
    clang_format = shutil.which("clang-format")
    if clang_format is None:
        pytest.skip("clang-format is not on PATH")
    for source in sorted(package.directory.rglob("*.cpp")):
        formatted = subprocess.run(
            [clang_format, "--style=file", str(source)],
            capture_output=True,
            text=True,
            cwd=package.directory,
            check=True,
        ).stdout
        assert formatted == source.read_text("utf-8"), source.name


def test_the_cpp_template_seeds_a_line_coverage_floor(tmp_path: Path) -> None:
    """A native member is judged like the others: its contract carries a floor."""
    from livery.workshop._backends import _python

    package = _render_cpp(tmp_path)
    assert _python.coverage_floor(package) == 100.0


def test_discovery_requires_pyproject_only_of_python_kinds(tmp_path: Path) -> None:
    packages_dir = tmp_path / "packages"
    (packages_dir / "native").mkdir(parents=True)
    (packages_dir / "native" / "workshop.toml").write_text(
        'kind = "cpp-conan"\nname = "acme-native"\n'
    )
    found = discover_packages(tmp_path)
    assert [package.name for package in found] == ["acme-native"]
    # A python member without its pyproject still refuses.
    (packages_dir / "member").mkdir(parents=True)
    (packages_dir / "member" / "workshop.toml").write_text(
        'kind = "python"\nname = "acme-member"\n'
    )
    with pytest.raises(ValueError, match=r"member: no pyproject\.toml"):
        discover_packages(tmp_path)


def test_python_verbs_skip_by_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    py = _package(tmp_path / "packages" / "member", "acme-member", "python")
    native = _package(tmp_path / "packages" / "native", "acme-native", "cpp-conan")
    for verb in ("typecheck", "typecomplete"):
        assert gated((py, native), verb) == (py,)
        out = capsys.readouterr().out
        assert f"{verb}: packages/native skips (cpp-conan kind)" in out
    # format and lint stay: the conanfile is python and ruff gates it.
    # test applies too: ctest is the kind's own check under that role,
    # so the role no longer skips by name for a native package.
    for verb in ("format", "lint", "test"):
        assert gated((py, native), verb) == (py, native)
        assert "skips" not in capsys.readouterr().out


def test_a_pure_python_workspace_gate_is_unchanged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    py = _package(tmp_path / "packages" / "member", "acme-member", "python")
    for verb in CiContract().check_verbs:
        assert gated((py,), verb) == (py,)
    # No package check judges a python package: the walk schedules
    # none of the native records for a workspace of python packages.
    ctx = GateContext(root=tmp_path, packages=(py,))
    native_checks = {"clang-format", "configure", "build", "ctest", "clang-tidy"}
    assert not native_checks & set(judges(ctx))
    assert capsys.readouterr().out == ""


def test_the_native_checks_run_per_package_in_order(
    restored_registry, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from dataclasses import replace

    ran: list[tuple[str, str, tuple[str, ...]]] = []
    for name in ("clang-format", "configure", "build", "ctest", "clang-tidy"):
        record = check_for(name)

        def spy(ctx: GateContext, name: str = name) -> None:
            assert ctx.package is not None
            ran.append((name, ctx.package.name, ctx.selection))

        register_check(replace(record, run=spy, fix=None))
    native = _package(tmp_path / "packages" / "native", "acme-native", "cpp-conan")

    def run_package(ctx: GateContext) -> str:
        for name in judges(ctx):
            if check_for(name).scope == "package":
                run_check(name, ctx)
        return capsys.readouterr().out

    out = run_package(GateContext(root=tmp_path, packages=(native,)))
    for name in ("clang-format", "configure", "build", "ctest", "clang-tidy"):
        assert f"  {name}: packages/native runs (cpp-conan kind)" in out
    assert [name for name, _, _ in ran] == [
        "clang-format",
        "configure",
        "build",
        "ctest",
        "clang-tidy",
    ]
    # A selection: the build first, since the kind's tests run on a
    # build, then the chosen ctest alone; the formatter and the linter
    # sit out a change confined to the tests.
    ran.clear()
    scoped = GateContext(
        root=tmp_path,
        packages=(native,),
        subset=(native,),
        tests={"packages/native": ("packages/native/tests/test_native.cpp",)},
    )
    out = run_package(scoped)
    assert "clang-format" not in out
    assert ran == [
        ("configure", "acme-native", ("tests/test_native.cpp",)),
        ("build", "acme-native", ("tests/test_native.cpp",)),
        ("ctest", "acme-native", ("tests/test_native.cpp",)),
    ]


def test_host_tools_are_named_when_missing(restored_registry, tmp_path: Path) -> None:
    from livery.workshop._env_tasks import missing_host_tools

    (tmp_path / "packages" / "member").mkdir(parents=True)
    (tmp_path / "packages" / "member" / "workshop.toml").write_text(
        'kind = "python"\nname = "acme-member"\n'
    )
    (tmp_path / "packages" / "member" / "pyproject.toml").write_text(
        '[project]\nname = "acme-member"\n'
    )
    assert missing_host_tools(tmp_path) == ()

    class _Idle:
        def build(self, package: Package, root: Path, *, epoch: int = 0) -> Path:
            return package.directory

        def module_roots(self, package: Package) -> tuple[str, ...]:
            return ()

        def referenced_siblings(
            self, package: Package, around: Neighbours
        ) -> dict[str, str]:
            return {}

        def publish_artifact(
            self,
            package: Package,
            root: Path,
            *,
            version: str,
            target: RegistryTarget,
        ) -> bool:
            return True

        def gate_build(self, package: Package, root: Path) -> None:
            return None

        def test(
            self,
            package: Package,
            root: Path,
            *,
            selection: tuple[str, ...] = (),
            pages: tuple[str, ...] = (),
        ) -> None:
            return None

        def check(self, package: Package, root: Path) -> None:
            return None

        def current_version(self, package: Package) -> str:
            return "0.0.1"

        def stamp_version(self, package: Package) -> _FakeStamper:
            return _FakeStamper()

        def declared_requirements(self, package: Package) -> dict[str, str]:
            return {}

        def declare_requirement(
            self, package: Package, dependency: Package, floor: str
        ) -> list[str]:
            return []

    register_kind(
        KindRecord(
            name="cpp-fake",
            backend=_Idle(),
            host_tools=("surely-absent-compiler",),
        )
    )
    (tmp_path / "packages" / "native").mkdir(parents=True)
    (tmp_path / "packages" / "native" / "workshop.toml").write_text(
        'kind = "cpp-fake"\nname = "acme-native"\n'
    )
    assert missing_host_tools(tmp_path) == ("surely-absent-compiler",)


# The registry facts.


def test_the_kind_registers_alone_in_the_chain() -> None:
    # Alone in the KIND chain (no python parent); the shared base
    # template still renders first, carrying the docs seeds.
    assert template_chain("package-cpp-conan") == (
        "package-base",
        "package-cpp-conan",
    )
    assert managed_files("cpp-conan") == (".clang-format", ".clang-tidy", "cliff.toml")
    assert not is_python_kind("cpp-conan")
    assert is_python_kind("python")
    record = record_for_template("package-cpp-conan")
    assert record is not None and record.name == "cpp-conan"
    assert record_for_template("package-layer") is None
    # The build tools are the kind's; clang-format and clang-tidy ride
    # their check records, which is where the profile reads them.
    assert kind_for("cpp-conan").tools == (
        "cmake",
        "conan",
        "ninja",
        "dotnet_coverage@windows",
    )
    from livery.workshop._checks import tools_for_kind

    # ruff rides in too: it judges the package's conanfile.py.
    assert {tool for tool, _ in tools_for_kind("cpp-conan")} == {
        "clang_format",
        "clang_tidy",
        "ruff",
    }


def test_the_project_render_wires_only_python_members(tmp_path: Path) -> None:
    destination = tmp_path / "scratch"
    answers = dict(read_answers(ROOT / ".copier-answers.yml"))
    answers["packages"] = [
        {"dir": "alpha", "name": "acme-alpha", "dev": "acme-alpha"},
        {"dir": "native", "name": "acme-native", "kind": "cpp-conan"},
    ]
    from livery.workshop._templates import compose_fragments

    data = {**answers, "kind": "project"}
    render(str(TEMPLATES), destination, {**data, "fragments": compose_fragments(data)})
    pyproject = (destination / "pyproject.toml").read_text()
    assert '"packages/alpha"' in pyproject
    assert 'members = ["packages/alpha"]' in pyproject
    assert "acme-native" not in pyproject
    # The cpp member is skipped whole, and every member's conan
    # recipe with it: the checker that reads the tree cannot resolve
    # the conan import, which lives in conan's own interpreter.
    assert 'exclude = ["packages/native", "packages/*/conanfile.py"]' in pyproject
    assert '"packages/native/src"' not in pyproject


# The armed leg: the fixture builds and its ctest passes.


@needs_toolchain
def test_the_rendered_package_builds_and_its_ctest_passes(tmp_path: Path) -> None:
    package = _render_cpp(tmp_path)
    assert (package.directory / "conanfile.py").is_file()
    assert (package.directory / "CMakeLists.txt").is_file()
    assert (package.directory / "src" / "native.cpp").is_file()
    # The kind's records, in the order the gate runs them.
    _cpp_conan.format_check(package)
    _cpp_conan.gate_build(package, tmp_path)
    _cpp_conan.test(package, tmp_path)
    _cpp_conan.lint(package, tmp_path)
    assert (package.directory / _cpp_conan.GATE_BUILD_DIR).is_dir()


@needs_toolchain
def test_the_rendered_package_configures_from_its_preset(tmp_path: Path) -> None:
    """A person opens the package in an editor and it configures.

    The preset carries the generator, the build directory, the build
    type and the compile commands, so `cmake --preset release` needs
    no argument of its own; an editor that reads CMakePresets.json
    gets the same configuration the gate uses.
    """
    import subprocess

    package = _render_cpp(tmp_path)
    presets = package.directory / "CMakePresets.json"
    assert presets.is_file()
    for name in ("release", "debug"):
        done = subprocess.run(
            ["cmake", "--preset", name],
            cwd=package.directory,
            capture_output=True,
            text=True,
            check=False,
        )
        assert done.returncode == 0, done.stdout + done.stderr
        build = package.directory / "build" / name
        assert (build / "compile_commands.json").is_file()
    built = subprocess.run(
        ["cmake", "--build", "--preset", "release"],
        cwd=package.directory,
        capture_output=True,
        text=True,
        check=False,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    tested = subprocess.run(
        ["ctest", "--preset", "release"],
        cwd=package.directory,
        capture_output=True,
        text=True,
        check=False,
    )
    assert tested.returncode == 0, tested.stdout + tested.stderr


@needs_toolchain
def test_a_misformatted_source_turns_the_gate_red_naming_the_file(
    tmp_path: Path,
) -> None:
    """The format check is the package's own .clang-format, applied."""
    package = _render_cpp(tmp_path)
    source = package.directory / "src" / "native.cpp"
    source.write_text(source.read_text().replace("const char*", "const  char  *"))
    with pytest.raises(_FAILURES, match="clang-format would rewrite") as caught:
        _cpp_conan.format_check(package)
    assert "native.cpp" in str(caught.value)
    # --fix heals it, and the check is green on the second pass.
    _cpp_conan.format_check(package, fix=True)
    _cpp_conan.format_check(package)


@needs_toolchain
def test_a_tidy_finding_turns_the_gate_red(tmp_path: Path) -> None:
    """The lint check is the package's own .clang-tidy, over the gate build."""
    package = _render_cpp(tmp_path)
    source = package.directory / "src" / "native.cpp"
    source.write_text(
        source.read_text().replace(
            "} // namespace native",
            "int branch(int a) {\n"
            "    if (a > 0) {\n"
            "        return 1;\n"
            "    } else {\n"
            "        return 1;\n"
            "    }\n"
            "}\n"
            "\n"
            "} // namespace native",
        )
    )
    _cpp_conan.gate_build(package, tmp_path)
    with pytest.raises(_FAILURES, match="clang-tidy found something") as caught:
        _cpp_conan.lint(package, tmp_path)
    assert "branch" in str(caught.value) or "bugprone" in str(caught.value)


def test_the_format_refusal_names_a_windows_path_whole() -> None:
    """A drive letter is part of the path, not the end of a field.

    clang-format writes `<file>:<line>:<column>: error: …`, and a
    Windows file name carries a colon of its own two characters in.
    Reading the path up to the line number keeps it whole, which is
    how the refusal came to say `D` on a Windows leg.
    """
    posix = "src/native.cpp:10:2: error: code should be clang-formatted"
    windows = (
        r"D:\a\livery\packages\native\src\native.cpp:10:2:"
        " error: code should be clang-formatted"
    )
    warned = "include/native.hpp:3:1: warning: code should be clang-formatted"
    assert _cpp_conan.unformatted("\n".join([posix, windows, warned])) == [
        r"D:\a\livery\packages\native\src\native.cpp",
        "include/native.hpp",
        "src/native.cpp",
    ]
    assert _cpp_conan.unformatted("nothing to say here") == []


@needs_toolchain
def test_a_green_ctest_run_is_measured_by_the_compilers_own_measurer(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The gate build is instrumented for its compiler family and the run leaves lines.

    gcov beside gcc, llvm-cov beside clang: whichever this host
    builds with, the part names the package's source with hit
    counts, and the local preview reads a number from it.
    """
    from livery.workshop import _coverage_lines as lines_
    from livery.workshop._backends import _python

    package = _render_cpp(tmp_path)
    _cpp_conan.gate_build(package, tmp_path)
    compiler_id, compiler = _cpp_conan.compiler_of(package)
    assert lines_.measurer_for(compiler_id) in ("gcov", "llvm"), compiler_id
    assert compiler
    _cpp_conan.test(package, tmp_path)
    parts = lines_.read_parts(tmp_path)
    assert list(parts) == ["packages/native"]
    sources = [
        name
        for name in parts["packages/native"]
        if name.startswith("packages/native/src/")
    ]
    assert sources, parts["packages/native"]
    assert any(
        hits > 0 for name in sources for hits in parts["packages/native"][name].values()
    )
    measured = _python.measured_coverage(tmp_path, (package,))
    assert 0.0 < measured["packages/native"] <= 100.0
    assert "coverage: " in capsys.readouterr().out
    # The selected arm measures its run too, afresh: the counters of
    # the full run do not leak into it.
    _cpp_conan.test(package, tmp_path, selection=("tests/test_native.cpp",))
    assert list(lines_.read_parts(tmp_path)) == ["packages/native"]


# The toolchain environment: refusals first, then the entered one.


@pytest.mark.usefixtures("fresh_toolchain")
def test_the_toolchain_environment_is_this_process_s_own_off_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_cpp_conan, "_on_windows", lambda: False)
    monkeypatch.delenv("CXX", raising=False)
    monkeypatch.setenv("WORKSHOP_PROBE", "here")
    env = _cpp_conan.toolchain_env()
    assert env["WORKSHOP_PROBE"] == "here"
    assert "CXX" not in env
    # A copy each call: a caller's additions never reach the next.
    env["CTEST_OUTPUT_ON_FAILURE"] = "1"
    assert "CTEST_OUTPUT_ON_FAILURE" not in _cpp_conan.toolchain_env()


@pytest.mark.usefixtures("fresh_toolchain")
def test_a_chosen_compiler_is_left_alone_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_cpp_conan, "_on_windows", lambda: True)
    monkeypatch.setenv("CXX", "clang-cl")
    monkeypatch.setattr(_cpp_conan, "_asked", _never_asked)
    env = _cpp_conan.toolchain_env()
    assert env["CXX"] == "clang-cl"
    assert "CC" not in env or env["CC"] != "cl"


def _never_asked(argv: list[str]) -> str:
    raise AssertionError(f"vswhere was asked: {argv}")


@pytest.mark.usefixtures("fresh_toolchain")
def test_a_developer_prompt_builds_with_cl_without_asking_vswhere(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cl on PATH is a developer prompt: CMake is pointed at it, nothing is entered."""
    prompt = tmp_path / "prompt"
    prompt.mkdir()
    for name in ("cl", "cl.exe"):
        (prompt / name).write_text("")
        (prompt / name).chmod(0o755)
    monkeypatch.setattr(_cpp_conan, "_on_windows", lambda: True)
    monkeypatch.delenv("CXX", raising=False)
    monkeypatch.delenv("CC", raising=False)
    monkeypatch.setenv("PATH", str(prompt))
    monkeypatch.setattr(_cpp_conan, "_asked", _never_asked)
    env = _cpp_conan.toolchain_env()
    assert (env["CC"], env["CXX"]) == ("cl", "cl")
    assert env["PATH"] == str(prompt)


@pytest.mark.usefixtures("fresh_toolchain")
def test_entering_msvc_refuses_without_the_installer_naming_the_workload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_cpp_conan, "_on_windows", lambda: True)
    monkeypatch.delenv("CXX", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("PROGRAMFILES(X86)", str(tmp_path / "pf"))
    with pytest.raises(
        _FAILURES, match="Visual Studio's installer is not at"
    ) as caught:
        _cpp_conan.toolchain_env()
    assert "vswhere.exe" in str(caught.value)
    assert "C++ workload" in str(caught.value)
    assert "set CXX" in str(caught.value)


@pytest.mark.usefixtures("fresh_toolchain")
def test_entering_msvc_refuses_when_no_installation_has_the_cpp_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vswhere = _cpp_conan.vswhere_path({"PROGRAMFILES(X86)": str(tmp_path / "pf")})
    vswhere.parent.mkdir(parents=True)
    vswhere.write_text("")
    monkeypatch.setattr(_cpp_conan, "_on_windows", lambda: True)
    monkeypatch.setattr(platform, "machine", lambda: "AMD64")
    monkeypatch.delenv("CXX", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("PROGRAMFILES(X86)", str(tmp_path / "pf"))
    asked: list[list[str]] = []

    def _nothing(argv: list[str]) -> str:
        asked.append(argv)
        return ""

    monkeypatch.setattr(_cpp_conan, "_asked", _nothing)
    with pytest.raises(_FAILURES, match="no Visual Studio installation has the"):
        _cpp_conan.toolchain_env()
    (argv,) = asked
    assert argv[0] == str(vswhere)
    assert argv[argv.index("-requires") + 1] == (
        "Microsoft.VisualStudio.Component.VC.Tools.x86.x64"
    )
    assert argv[-2:] == ["-property", "installationPath"]


@pytest.mark.usefixtures("fresh_toolchain")
def test_entering_msvc_refuses_a_batch_file_that_left_no_toolset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vswhere = _cpp_conan.vswhere_path({"PROGRAMFILES(X86)": str(tmp_path / "pf")})
    vswhere.parent.mkdir(parents=True)
    vswhere.write_text("")
    monkeypatch.setattr(_cpp_conan, "_on_windows", lambda: True)
    monkeypatch.setattr(platform, "machine", lambda: "ARM64")
    monkeypatch.delenv("CXX", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("PROGRAMFILES(X86)", str(tmp_path / "pf"))
    monkeypatch.setattr(_cpp_conan, "_asked", lambda argv: "C:\\VS")
    scripts: list[str] = []

    def _no_toolset(script: str) -> str:
        scripts.append(script)
        return "PATH=C:\\Windows\n"

    monkeypatch.setattr(_cpp_conan, "_shell_set", _no_toolset)
    with pytest.raises(_FAILURES, match="left no VCToolsInstallDir"):
        _cpp_conan.toolchain_env()
    (script,) = scripts
    # The ARM64 host enters its own tools' batch file, output hidden,
    # and reads the environment back with set.
    assert script.startswith('@call "')
    assert "vcvarsarm64.bat" in script
    assert script.endswith('" >nul\n@set\n')


@pytest.mark.usefixtures("fresh_toolchain")
def test_the_entered_environment_is_read_once_and_points_cmake_at_cl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vswhere = _cpp_conan.vswhere_path({"PROGRAMFILES(X86)": str(tmp_path / "pf")})
    vswhere.parent.mkdir(parents=True)
    vswhere.write_text("")
    monkeypatch.setattr(_cpp_conan, "_on_windows", lambda: True)
    monkeypatch.setattr(platform, "machine", lambda: "AMD64")
    monkeypatch.delenv("CXX", raising=False)
    monkeypatch.delenv("CC", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("PROGRAMFILES(X86)", str(tmp_path / "pf"))
    asked: list[list[str]] = []

    def _installation(argv: list[str]) -> str:
        asked.append(argv)
        return "C:\\VS"

    monkeypatch.setattr(_cpp_conan, "_asked", _installation)
    monkeypatch.setattr(
        _cpp_conan,
        "_shell_set",
        lambda script: (
            "=C:=C:\\work\n"
            "Path=C:\\VS\\VC\\bin;C:\\Windows\n"
            "VCToolsInstallDir=C:\\VS\\VC\\Tools\\MSVC\\14.51\\\n"
            "INCLUDE=C:\\VS\\VC\\include\n"
            "no equals sign here\n"
        ),
    )
    env = _cpp_conan.toolchain_env()
    assert env["PATH"] == "C:\\VS\\VC\\bin;C:\\Windows"
    assert env["VCTOOLSINSTALLDIR"] == "C:\\VS\\VC\\Tools\\MSVC\\14.51\\"
    assert env["INCLUDE"] == "C:\\VS\\VC\\include"
    assert (env["CC"], env["CXX"]) == ("cl", "cl")
    assert not any(key.startswith("=") or key == "" for key in env)
    assert "no equals sign here" not in env
    # Entered once: the second call copies what the first read.
    again = _cpp_conan.toolchain_env()
    assert again == env
    assert len(asked) == 1


# The MSVC measurer, with the engine and ctest faked: refusals first.


_GREEN = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<testsuite name="ctest" tests="1" failures="0" disabled="0" skipped="0">'
    '<testcase name="test_native" status="run"/></testsuite>\n'
)
_RED = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<testsuite name="ctest" tests="2" failures="1" disabled="0" skipped="0">'
    '<testcase name="test_native" status="fail"/>'
    '<testcase name="test_other" status="run"/></testsuite>\n'
)
_EMPTY = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<testsuite name="ctest" tests="0" failures="0" disabled="0" skipped="0">'
    "</testsuite>\n"
)


def _cobertura(package: Package) -> str:
    """The engine's report for one source file: line 5 reached, line 6 not."""
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<coverage line-rate="0.5">'
        f"<sources><source>{package.directory}</source></sources>"
        '<packages><package name="test_native.exe"><classes>'
        '<class name="native.cpp" filename="src/native.cpp" line-rate="0.5">'
        '<lines><line number="5" hits="1"/><line number="6" hits="0"/></lines>'
        "</class></classes></package></packages></coverage>\n"
    )


class _FakeEngine:
    """A dotnet-coverage handle: instruments by writing a copy, collects as told."""

    def __init__(
        self,
        *,
        junit: str | None,
        report: str | None = None,
        instrument_code: int = 0,
        collect_code: int = 0,
        deployed: bool = True,
    ) -> None:
        self.junit = junit
        self.report = report
        self.instrument_code = instrument_code
        self.collect_code = collect_code
        self.deployed = deployed
        self.calls: list[tuple[str, ...]] = []
        self.envs: list[dict[str, str]] = []
        self.ran = b""

    def opts(self, **kwargs: object) -> object:
        env = kwargs.get("env")

        def _run(*args: str) -> object:
            if not self.deployed:
                raise OSError(2, "No such file or directory", "dotnet-coverage")
            self.calls.append(args)
            self.envs.append(dict(env) if isinstance(env, dict) else {})
            if args[0] == "instrument":
                if self.instrument_code:
                    return SimpleNamespace(
                        code=self.instrument_code, stdout="", stderr="no symbols"
                    )
                out = Path(args[args.index("-o") + 1])
                out.write_bytes(b"MZ instrumented")
                # The engine writes the copy's own symbols and its
                # runtime beside it.
                out.with_suffix(".pdb").write_bytes(b"pdb")
                (out.parent / "static_covrun64.dll").write_bytes(b"MZ runtime")
                return SimpleNamespace(
                    code=0, stdout="Input file successfully instrumented.", stderr=""
                )
            build_dir = Path(args[args.index("--test-dir") + 1])
            self.ran = (build_dir / "test_native.exe").read_bytes()
            if self.junit is not None:
                Path(args[args.index("--output-junit") + 1]).write_text(self.junit)
            if self.report is not None:
                Path(args[args.index("-o") + 1]).write_text(self.report)
            return SimpleNamespace(
                code=self.collect_code,
                stdout="1/1 Test #1: test_native ... Passed\n",
                stderr="",
            )

        return _run


def _fake_ctest(exe: Path) -> SimpleNamespace:
    """A ctest handle that names *exe* as the one test's command."""

    def _opts(**kwargs: object) -> object:
        def _run(*args: str) -> object:
            listing = json.dumps({"tests": [{"command": [str(exe)]}]})
            return SimpleNamespace(code=0, stdout=listing, stderr="")

        return _run

    return SimpleNamespace(opts=_opts)


def _msvc_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, engine: _FakeEngine
) -> tuple[Package, Path]:
    """A rendered package whose gate build says MSVC, one test executable, fakes in."""
    from livery.toolroom import tools

    package = _render_cpp(tmp_path)
    build_dir = package.directory / _cpp_conan.GATE_BUILD_DIR
    compiler = build_dir / "CMakeFiles" / "4.4.2" / "CMakeCXXCompiler.cmake"
    compiler.parent.mkdir(parents=True)
    compiler.write_text(
        'set(CMAKE_CXX_COMPILER "C:/VS/VC/Tools/MSVC/14.51/bin/Hostx64/x64/cl.exe")\n'
        'set(CMAKE_CXX_COMPILER_ID "MSVC")\n'
    )
    exe = build_dir / "test_native.exe"
    exe.write_bytes(b"MZ original")
    monkeypatch.setattr(tools, "ctest", _fake_ctest(exe))
    monkeypatch.setattr(tools, "dotnet_coverage", engine, raising=False)
    monkeypatch.setattr(_cpp_conan, "_ctest_program", lambda: "C:/store/ctest.exe")
    monkeypatch.setattr(_cpp_conan, "toolchain_env", lambda: dict(os.environ))
    return package, exe


def _leftovers(exe: Path) -> list[str]:
    return sorted(p.name for p in exe.parent.glob("*instrumented*"))


def test_the_msvc_measurer_refuses_an_undeployed_engine_naming_the_requirement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop import _coverage_lines as lines_

    engine = _FakeEngine(junit=_GREEN, deployed=False)
    package, exe = _msvc_gate(tmp_path, monkeypatch, engine)
    with pytest.raises(_FAILURES, match="dotnet-coverage is not on PATH") as caught:
        _cpp_conan.test(package, tmp_path)
    assert '"dotnet_coverage" to [tools] requires' in str(caught.value)
    assert "tools.lock" in str(caught.value)
    assert exe.read_bytes() == b"MZ original"
    assert lines_.read_parts(tmp_path) == {}


def test_the_msvc_measurer_refuses_a_build_naming_no_test_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _FakeEngine(junit=_GREEN)
    package, exe = _msvc_gate(tmp_path, monkeypatch, engine)
    exe.unlink()
    with pytest.raises(_FAILURES, match="names no test executable to instrument"):
        _cpp_conan.test(package, tmp_path)
    assert engine.calls == []


def test_the_msvc_measurer_refuses_an_instrumentation_that_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop import _coverage_lines as lines_

    engine = _FakeEngine(junit=_GREEN, instrument_code=1)
    package, exe = _msvc_gate(tmp_path, monkeypatch, engine)
    with pytest.raises(
        _FAILURES, match=r"could not instrument test_native.exe \(exit 1\)"
    ) as caught:
        _cpp_conan.test(package, tmp_path)
    assert "no symbols" in str(caught.value)
    assert [call[0] for call in engine.calls] == ["instrument"]
    assert exe.read_bytes() == b"MZ original"
    assert _leftovers(exe) == []
    assert lines_.read_parts(tmp_path) == {}


def test_a_red_ctest_under_the_engine_is_read_from_ctest_s_own_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The collector's exit code is its own: the verdict is ctest's JUnit report."""
    from livery.workshop import _coverage_lines as lines_

    engine = _FakeEngine(junit=_RED, collect_code=0)
    package, exe = _msvc_gate(tmp_path, monkeypatch, engine)
    engine.report = _cobertura(package)
    with pytest.raises(_FAILURES, match=r"ctest failed \(1 of 2 test\(s\)\)") as caught:
        _cpp_conan.test(package, tmp_path)
    assert "test_native ... Passed" in str(caught.value)
    # The instrumented copy ran in the original's place, and the
    # original is back whatever the verdict.
    assert engine.ran == b"MZ instrumented"
    assert exe.read_bytes() == b"MZ original"
    assert _leftovers(exe) == []
    assert lines_.read_parts(tmp_path) == {}


def test_the_msvc_measurer_refuses_a_run_that_left_no_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _FakeEngine(junit=None, collect_code=1)
    package, exe = _msvc_gate(tmp_path, monkeypatch, engine)
    with pytest.raises(_FAILURES, match=r"ctest left no report at ctest\.xml"):
        _cpp_conan.test(package, tmp_path)
    assert exe.read_bytes() == b"MZ original"


def test_a_selection_no_ctest_answers_to_is_a_refusal_under_the_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _FakeEngine(junit=_EMPTY)
    package, _exe = _msvc_gate(tmp_path, monkeypatch, engine)
    with pytest.raises(_FAILURES, match="no ctest is named test_missing"):
        _cpp_conan.test(package, tmp_path, selection=("tests/test_missing.cpp",))
    collect = engine.calls[-1]
    assert collect[collect.index("-R") + 1] == "^(test_missing)$"
    engine.calls.clear()
    with pytest.raises(_FAILURES, match="ctest ran no test; register one"):
        _cpp_conan.test(package, tmp_path)
    assert "-R" not in engine.calls[-1]


def test_the_msvc_measurer_refuses_a_collector_that_failed_after_a_green_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _FakeEngine(junit=_GREEN, collect_code=3)
    package, _exe = _msvc_gate(tmp_path, monkeypatch, engine)
    with pytest.raises(_FAILURES, match="dotnet-coverage exited 3 collecting"):
        _cpp_conan.test(package, tmp_path)


def test_the_msvc_measurer_refuses_a_green_run_without_a_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _FakeEngine(junit=_GREEN)
    package, _exe = _msvc_gate(tmp_path, monkeypatch, engine)
    with pytest.raises(_FAILURES, match="dotnet-coverage wrote no report at"):
        _cpp_conan.test(package, tmp_path)


def test_a_green_ctest_run_under_the_engine_is_measured_from_its_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.workshop import _coverage_lines as lines_

    engine = _FakeEngine(junit=_GREEN)
    package, exe = _msvc_gate(tmp_path, monkeypatch, engine)
    engine.report = _cobertura(package)
    _cpp_conan.test(package, tmp_path)
    assert lines_.read_parts(tmp_path) == {
        "packages/native": {"packages/native/src/native.cpp": {5: 1, 6: 0}}
    }
    instrument, collect = engine.calls
    assert instrument[:2] == ("instrument", "--nologo")
    assert instrument[-1] == str(exe)
    assert instrument[instrument.index("-o") + 1].endswith(
        "test_native.instrumented.exe"
    )
    assert collect[:2] == ("collect", "--nologo")
    assert collect[collect.index("-f") + 1] == "cobertura"
    assert collect[collect.index("--") + 1] == "C:/store/ctest.exe"
    assert "--test-dir" in collect
    assert "--output-on-failure" in collect
    assert "--output-junit" in collect
    assert "-R" not in collect
    assert engine.ran == b"MZ instrumented"
    assert exe.read_bytes() == b"MZ original"
    assert _leftovers(exe) == []
    assert not (exe.parent / "static_covrun64.dll").exists()
    settings = (exe.parent / "coverage" / "coverage.config").read_text()
    assert "<EnableStaticNativeInstrumentation>True" in settings
    assert "<CollectFromChildProcesses>True" in settings
    assert "[tT][eE][sS][tT]_[nN][aA][tT][iI][vV][eE]" in settings
    assert engine.envs[-1]["DOTNET_COVERAGE_TELEMETRY_OPTOUT"] == "1"
    assert engine.envs[-1]["DOTNET_COVERAGE_NOLOGO"] == "1"
    assert "coverage: 1 file(s) measured by msvc" in capsys.readouterr().out


def test_the_engine_s_own_runtime_is_never_an_object_to_instrument(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A runtime an earlier run left is the engine's, never an object."""
    engine = _FakeEngine(junit=_GREEN)
    package, exe = _msvc_gate(tmp_path, monkeypatch, engine)
    engine.report = _cobertura(package)
    lingering = exe.parent / "static_covrun32.dll"
    lingering.write_bytes(b"MZ runtime")
    (exe.parent / "acme.dll").write_bytes(b"MZ library")
    _cpp_conan.test(package, tmp_path)
    instrumented = [call[-1] for call in engine.calls if call[0] == "instrument"]
    assert instrumented == [str(exe), str(exe.parent / "acme.dll")]
    # A file that was there before the run stays: the sweep takes
    # only what this run's engine wrote.
    assert lingering.is_file()


@needs_msvc
def test_a_green_ctest_run_built_with_msvc_is_measured_by_microsoft_s_engine(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The Windows leg: enter MSVC, build, instrument, collect, read; then a red run."""
    from livery.workshop import _coverage_lines as lines_
    from livery.workshop._backends import _python

    package = _render_cpp(tmp_path)
    _cpp_conan.gate_build(package, tmp_path)
    compiler_id, compiler = _cpp_conan.compiler_of(package)
    assert compiler_id == "MSVC", (compiler_id, compiler)
    _cpp_conan.test(package, tmp_path)
    parts = lines_.read_parts(tmp_path)
    assert list(parts) == ["packages/native"]
    sources = [
        name
        for name in parts["packages/native"]
        if name.startswith("packages/native/src/")
    ]
    assert sources, parts["packages/native"]
    assert any(
        hits > 0 for name in sources for hits in parts["packages/native"][name].values()
    )
    measured = _python.measured_coverage(tmp_path, (package,))
    assert 0.0 < measured["packages/native"] <= 100.0
    assert "measured by msvc" in capsys.readouterr().out
    build_dir = package.directory / _cpp_conan.GATE_BUILD_DIR
    assert (build_dir / "test_native.exe").is_file()
    assert not list(build_dir.rglob("*instrumented*"))
    # A red test fails the run with ctest's output, read from ctest's
    # own report under the collector.
    test_file = package.directory / "tests" / "test_native.cpp"
    test_file.write_text(test_file.read_text().replace("return 0;", "return 1;"))
    _cpp_conan.gate_build(package, tmp_path)
    with pytest.raises(_FAILURES, match=r"ctest failed \(1 of 1 test"):
        _cpp_conan.test(package, tmp_path)
