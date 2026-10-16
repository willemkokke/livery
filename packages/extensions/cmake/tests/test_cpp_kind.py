"""The cmake extension's gate build: refusals and skips first, then the armed build.

Its three steps and the mount that registers them, the configure,
compile and ctest of a C++ package born from conan's template, the
measured run, the toolchain environment on Windows, and the MSVC
measurer with Microsoft's engine and ctest faked.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

from livery.extensions.cmake import _build as _cmake
from livery.workshop import Package, PhaseContext
from livery.workshop._steps import (
    StepRecord,
    judges,
    register_step,
    run_step,
    step_for,
)

_FAILURES = (BaseException,)


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
    or not _cmake.vswhere_path().is_file()
    or bool(_MISSING_MSVC),
    reason="the MSVC leg needs Windows, Visual Studio's installer, cmake and ninja",
)


@pytest.fixture
def hermetic_toolchain(monkeypatch: pytest.MonkeyPatch) -> None:
    """The gate's tools run in this process's environment, whatever the host has."""
    monkeypatch.setattr(_cmake, "toolchain_env", lambda: dict(os.environ))


@pytest.fixture
def fresh_toolchain() -> object:
    """The toolchain environment read afresh by this test, and by the next."""
    _cmake._entered.cache_clear()
    yield None
    _cmake._entered.cache_clear()


@pytest.fixture
def restored_registry() -> Iterator[None]:
    from livery.workshop import _steps

    steps = _steps.snapshot()
    yield
    _steps.restore(steps)


@pytest.fixture
def cmake_steps() -> Iterator[tuple[StepRecord, ...]]:
    """Register cmake's steps from its declaration, as the mount does; the records.

    The registry is put back after the test.
    """
    from livery.workshop import _steps
    from livery.workshop._extensions import declaration, register_declared

    found = declaration("cmake")
    assert found is not None
    state = _steps.snapshot()
    register_declared("cmake", found.additions)
    try:
        yield found.additions.steps
    finally:
        _steps.restore(state)


def _package(directory: Path, name: str, *extensions: str) -> Package:
    return Package(
        directory=directory,
        path=f"packages/{directory.name}",
        name=name,
        depends=(),
        extensions=extensions,
    )


def _undeployed(tool: str) -> str:
    """The workshop's refusal for *tool*, a tool of the store not on PATH."""
    from livery.workshop import undeployed

    with pytest.raises(_FAILURES) as taught:
        undeployed(tool)
    return str(taught.value)


def _listing_coverage(root: Path) -> None:
    """Write *root*'s contract listing coverage, which cmake measures for."""
    (root / "workshop.toml").write_text('[workspace]\nextensions = ["coverage"]\n')


#: The workspace a birth's seeds are written from: its identity, and the
#: extensions conan's ``cpp-package`` template needs, which its root
#: lists before the creation, since a creation installs nothing.
_IDENTITY = """\
[workspace]
name = "acme"
description = "The acme monorepo (virtual root)."
namespace = "acme"
authors = [{ name = "Acme", email = "dev@acme.test" }]
copyright-year = "2026"
extensions = ["cmake", "conan"]
"""

#: What a birth's data carries beyond the package's own names.
_FACTS = {
    "project_name": "acme",
    "project_description": "The acme monorepo (virtual root).",
    "namespace_package": "acme",
    "author_name": "Acme",
    "author_email": "dev@acme.test",
    "copyright_year": "2026",
    "runner_prog": "fm",
    "python_floor": "3.11",
    "forge_kind": "github",
    "forge_owner": "",
    "forge_url": "",
}


def _render_cpp(tmp_path: Path) -> Package:
    """A cpp-conan package, straight from conan's ``cpp-package`` template."""
    from livery.workshop._composition import closure
    from livery.workshop._extensions import installed_declaration
    from livery.workshop._seeds import create_package, derived
    from livery.workshop._shipped_files import settle_package

    destination = tmp_path / "packages" / "native"
    data: dict[str, object] = {
        **_FACTS,
        "package_name": "acme-native",
        "package_description": "acme-native: a native library.",
    }
    data.update(derived(data))
    with tempfile.TemporaryDirectory() as scratch:
        (Path(scratch) / "workshop.toml").write_text(_IDENTITY, encoding="utf-8")
        create_package(Path(scratch), destination, "cpp-package", data)
    # The native configs come from the step records, as they do at a
    # birth: the template ships none.
    settle_package(destination, closure(("cmake", "conan"), installed_declaration))
    return _package(destination, "acme-native", "cmake", "conan")


# The refusals and the skips first.


def _contract(root: Path, member: str, listed: str) -> None:
    """Give *root* a member *member* listing *listed*, with a project file."""
    directory = root / "packages" / member
    directory.mkdir(parents=True)
    (directory / "workshop.toml").write_text(
        f'name = "acme-{member}"\nextensions = {listed}\n'
    )
    (directory / "pyproject.toml").write_text(
        f'[project]\nname = "acme-{member}"\nversion = "0.1.0"\n'
    )


def test_a_workspace_without_cmake_registers_no_build_step(tmp_path: Path) -> None:
    from livery.footman import _registry as footman_registry
    from livery.workshop import _points
    from livery.workshop._extensions import mount_extensions
    from livery.workshop._steps import restore, snapshot, steps_by_name

    root = tmp_path / "ws"
    root.mkdir()
    (root / "workshop.toml").write_text("[workspace]\nextensions = []\n")
    _contract(root, "py", '["python"]')
    _contract(root, "native", '["nanobind"]')

    def registered() -> list[tuple[str, str]]:
        # The mount a process started now takes: the steps and the points'
        # jobs as a new process has them, and the verbs it makes in a
        # throwaway tree.
        restore(snapshot(initial=True))
        _points.restore(_points.snapshot(initial=True))
        with footman_registry.capture():
            mount_extensions(root)
        return [
            (name, record.extension)
            for name, record in steps_by_name().items()
            if name.startswith("build.") or name == "test.ctest"
        ]

    # python's, conan's and nanobind's builds serve the isolated side, the
    # release's, so no build step reaches the gate, and no ctest either.
    assert registered() == []
    # A package that lists cmake brings cmake's three, under its name.
    _contract(root, "lib", '["cmake", "conan"]')
    assert registered() == [
        ("build.configure", "cmake"),
        ("build.compile", "cmake"),
        ("test.ctest", "cmake"),
    ]


@pytest.mark.usefixtures("hermetic_toolchain")
def test_a_selected_test_refuses_when_ctest_is_not_deployed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ctest rides the cmake record, and its absence reads the same."""
    empty = tmp_path / "empty-path"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    package = _package(
        tmp_path / "packages" / "native", "acme-native", "cmake", "conan"
    )
    package.directory.mkdir(parents=True)
    with pytest.raises(_FAILURES) as caught:
        _cmake.test(package, tmp_path, selection=("tests/test_native.cpp",))
    assert str(caught.value) == _undeployed("ctest")


def _native_lines(root: Path) -> dict[str, dict[str, dict[int, int]]]:
    """The parts measured runs left at *root*: each file's lines by unit, unrun at 0."""
    from livery.extensions.coverage._parts import part_files, read_part

    found: dict[str, dict[str, dict[int, int]]] = {}
    for path in part_files(root):
        part = read_part(path, root)
        found[part.unit] = {
            name: {line: entry.counts.get(line, 0) for line in sorted(entry.statements)}
            for name, entry in part.files.items()
        }
    return found


def test_a_native_build_without_coverage_is_not_instrumented(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import livery.toolroom.tools as tools

    member = tmp_path / "packages" / "native"
    member.mkdir(parents=True)
    (member / "workshop.toml").write_text(
        'extensions = ["cmake", "conan"]\nname = "acme-native"\n'
    )
    (tmp_path / "workshop.toml").write_text("[workspace]\nextensions = []\n")
    package = _package(member, "acme-native", "cmake", "conan")
    configured: list[tuple[str, ...]] = []
    ran: list[dict[str, str]] = []

    def _cmake_opts(**_kwargs: object) -> object:
        def _run(*args: str) -> None:
            configured.append(args)

        return _run

    def _ctest_opts(**kwargs: object) -> object:
        env = kwargs.get("env")
        ran.append(dict(env) if isinstance(env, dict) else {})

        def _run(*args: str) -> object:
            return SimpleNamespace(code=0, stdout="", stderr="")

        return _run

    monkeypatch.setattr(tools, "cmake", SimpleNamespace(opts=_cmake_opts))
    monkeypatch.setattr(tools, "ctest", SimpleNamespace(opts=_ctest_opts))
    monkeypatch.setattr(_cmake, "toolchain_env", lambda: {})
    # Coverage unlisted: the configure takes no instrumentation and
    # removes the include an earlier measured configure left in CMake's
    # cache, and ctest runs alone, measured by nothing and leaving no part.
    _cmake.configure(package)
    assert "-UCMAKE_PROJECT_INCLUDE" in configured[-1]
    assert not [arg for arg in configured[-1] if str(_cmake.COVERAGE_CMAKE) in arg]
    _cmake.test(package, tmp_path)
    assert "LLVM_PROFILE_FILE" not in ran[-1]
    assert not (member / _cmake.GATE_BUILD_DIR / _cmake.PROFILE_DIR).exists()
    assert _native_lines(tmp_path) == {}
    assert "coverage" not in capsys.readouterr().out
    # Listed, cmake measures for coverage: the configure instruments.
    _listing_coverage(tmp_path)
    _cmake.configure(package)
    assert f"-DCMAKE_PROJECT_INCLUDE={_cmake.COVERAGE_CMAKE}" in configured[-1]
    assert "-UCMAKE_PROJECT_INCLUDE" not in configured[-1]


@needs_toolchain
def test_a_selection_no_ctest_answers_to_is_a_refusal(tmp_path: Path) -> None:
    package = _render_cpp(tmp_path)
    _cmake.gate_build(package, tmp_path)
    with pytest.raises(_FAILURES, match="no ctest is named test_missing"):
        _cmake.test(package, tmp_path, selection=("tests/test_missing.cpp",))


@needs_toolchain
def test_a_selected_test_runs_its_ctest_on_the_gate_build(tmp_path: Path) -> None:
    package = _render_cpp(tmp_path)
    _cmake.gate_build(package, tmp_path)
    _cmake.test(package, tmp_path, selection=("tests/test_native.cpp",))
    test_file = package.directory / "tests" / "test_native.cpp"
    test_file.write_text(test_file.read_text().replace("return 0;", "return 1;"))
    # The rebuild after the edit is what the reflex runs before the tests.
    _cmake.gate_build(package, tmp_path)
    with pytest.raises(_FAILURES, match="ctest failed"):
        _cmake.test(package, tmp_path, selection=("tests/test_native.cpp",))


@needs_toolchain
def test_a_red_ctest_is_a_refusal(tmp_path: Path) -> None:
    package = _render_cpp(tmp_path)
    test_file = package.directory / "tests" / "test_native.cpp"
    broken = test_file.read_text().replace("return 0;", "return 1;")
    test_file.write_text(broken)
    _cmake.gate_build(package, tmp_path)
    with pytest.raises(_FAILURES, match="ctest failed"):
        _cmake.test(package, tmp_path)


NATIVE_CHECKS = (
    "build.configure",
    "build.compile",
    "test.ctest",
)


def test_the_native_steps_run_per_package_in_order(
    restored_registry,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    cmake_steps: object,
) -> None:
    from dataclasses import replace

    ran: list[tuple[str, str, tuple[str, ...]]] = []
    for name in NATIVE_CHECKS:
        record = step_for(name)

        def spy(ctx: PhaseContext, name: str = name) -> None:
            assert ctx.package is not None
            ran.append((name, ctx.package.name, ctx.selection))

        register_step(replace(record, run=spy, fix=None))
    native = _package(tmp_path / "packages" / "native", "acme-native", "cmake", "conan")

    def run_package(ctx: PhaseContext) -> str:
        for name in judges(ctx):
            if step_for(name).scope == "package":
                run_step(name, ctx)
        return capsys.readouterr().out

    out = run_package(PhaseContext(root=tmp_path, packages=(native,)))
    for name in NATIVE_CHECKS:
        assert f"  {name}: packages/native runs (a cmake package)" in out
    assert [name for name, _, _ in ran] == list(NATIVE_CHECKS)
    # A selection: the build first, since the kind's tests run on a
    # build, then the chosen ctest alone; the formatter and the linter
    # sit out a change confined to the tests.
    ran.clear()
    scoped = PhaseContext(
        root=tmp_path,
        packages=(native,),
        subset=(native,),
        tests={"packages/native": ("packages/native/tests/test_native.cpp",)},
    )
    out = run_package(scoped)
    assert "clang-format" not in out
    assert ran == [
        ("build.configure", "acme-native", ("tests/test_native.cpp",)),
        ("build.compile", "acme-native", ("tests/test_native.cpp",)),
        ("test.ctest", "acme-native", ("tests/test_native.cpp",)),
    ]


def test_the_native_build_steps_hand_cmake_the_words_after_the_dashes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, cmake_steps: object
) -> None:
    from dataclasses import replace

    handed: list[tuple[str, tuple[str, ...]]] = []

    def configure(package: Package, arguments: tuple[str, ...]) -> None:
        handed.append(("configure", arguments))

    def compile_(package: Package, arguments: tuple[str, ...]) -> None:
        handed.append(("compile", arguments))

    def ctest(
        package: Package,
        root: Path,
        *,
        selection: tuple[str, ...],
        arguments: tuple[str, ...],
    ) -> None:
        handed.append(("ctest", arguments))

    monkeypatch.setattr(_cmake, "configure", configure)
    monkeypatch.setattr(_cmake, "compile", compile_)
    monkeypatch.setattr(_cmake, "test", ctest)
    native = _package(tmp_path / "packages" / "native", "acme-native", "cmake", "conan")
    words = ("-j", "4")
    ctx = PhaseContext(
        root=tmp_path, packages=(native,), package=native, arguments=words
    )
    for name in ("build.configure", "build.compile"):
        record = step_for(name)
        assert record.arguments
        record.run(ctx)
    assert handed == [("configure", words), ("compile", words)]
    # ctest names no tool: a tool a test step names is a verdict tool,
    # which no workspace serves from the host, and a workspace may serve
    # cmake from the host. So its verb takes no words after --, and the
    # step hands ctest the context's, which are none.
    record = step_for("test.ctest")
    assert not record.arguments
    record.run(replace(ctx, arguments=()))
    assert handed[-1] == ("ctest", ())


# The armed leg: the fixture builds and its ctest passes.


@needs_toolchain
def test_the_rendered_package_builds_and_its_ctest_passes(tmp_path: Path) -> None:
    package = _render_cpp(tmp_path)
    assert (package.directory / "conanfile.py").is_file()
    assert (package.directory / "CMakeLists.txt").is_file()
    assert (package.directory / "src" / "native.cpp").is_file()
    # The kind's records, in the order the gate runs them.
    _cmake.gate_build(package, tmp_path)
    _cmake.test(package, tmp_path)
    assert (package.directory / _cmake.GATE_BUILD_DIR).is_dir()
    database = _cmake.compile_commands(package)
    assert database is not None and database.is_file()


@needs_toolchain
def test_a_green_ctest_run_is_measured_by_the_compilers_own_measurer(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The gate build is instrumented for its compiler family and the run leaves lines.

    gcov beside gcc, llvm-cov beside clang: whichever this host
    builds with, the part names the package's source with hit
    counts, and the local preview reads a number from it.
    """
    from livery.extensions.coverage import _floors as coverage_floors

    package = _render_cpp(tmp_path)
    _listing_coverage(tmp_path)
    _cmake.gate_build(package, tmp_path)
    compiler_id, compiler = _cmake.compiler_of(package)
    assert _cmake.measurer_for(compiler_id) in ("gcov", "llvm"), compiler_id
    assert compiler
    _cmake.test(package, tmp_path)
    parts = _native_lines(tmp_path)
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
    # The most the measurer gives: the functions, demangled, and on clang
    # each stretch of code with its count.
    from livery.extensions.coverage._parts import part_files, read_part

    (written,) = part_files(tmp_path)
    part = read_part(written, tmp_path)
    entries = [part.files[name] for name in sources]
    names = [region.name for entry in entries for region in entry.regions]
    assert names and not [name for name in names if name.startswith("_Z")], names
    assert part.measurer.columns == (part.measurer.name == "llvm-cov")
    assert any(entry.ranges for entry in entries) == part.measurer.columns
    measured = coverage_floors.measured_coverage(tmp_path, (package,))
    assert 0.0 < measured["packages/native"] <= 100.0
    assert "coverage: " in capsys.readouterr().out
    # The selected arm measures its run too, afresh: the counters of
    # the full run do not leak into it.
    _cmake.test(package, tmp_path, selection=("tests/test_native.cpp",))
    assert list(_native_lines(tmp_path)) == ["packages/native"]


# The toolchain environment: refusals first, then the entered one.


@pytest.mark.usefixtures("fresh_toolchain")
def test_the_toolchain_environment_is_this_process_s_own_off_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_cmake, "_on_windows", lambda: False)
    monkeypatch.delenv("CXX", raising=False)
    monkeypatch.setenv("WORKSHOP_PROBE", "here")
    env = _cmake.toolchain_env()
    assert env["WORKSHOP_PROBE"] == "here"
    assert "CXX" not in env
    # A copy each call: a caller's additions never reach the next.
    env["CTEST_OUTPUT_ON_FAILURE"] = "1"
    assert "CTEST_OUTPUT_ON_FAILURE" not in _cmake.toolchain_env()


@pytest.mark.usefixtures("fresh_toolchain")
def test_a_chosen_compiler_is_left_alone_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_cmake, "_on_windows", lambda: True)
    monkeypatch.setenv("CXX", "clang-cl")
    monkeypatch.setattr(_cmake, "_asked", _never_asked)
    env = _cmake.toolchain_env()
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
    monkeypatch.setattr(_cmake, "_on_windows", lambda: True)
    monkeypatch.delenv("CXX", raising=False)
    monkeypatch.delenv("CC", raising=False)
    monkeypatch.setenv("PATH", str(prompt))
    monkeypatch.setattr(_cmake, "_asked", _never_asked)
    env = _cmake.toolchain_env()
    assert (env["CC"], env["CXX"]) == ("cl", "cl")
    assert env["PATH"] == str(prompt)


@pytest.mark.usefixtures("fresh_toolchain")
def test_entering_msvc_refuses_without_the_installer_naming_the_workload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_cmake, "_on_windows", lambda: True)
    monkeypatch.delenv("CXX", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("PROGRAMFILES(X86)", str(tmp_path / "pf"))
    with pytest.raises(
        _FAILURES, match="Visual Studio's installer is not at"
    ) as caught:
        _cmake.toolchain_env()
    assert "vswhere.exe" in str(caught.value)
    assert "C++ workload" in str(caught.value)
    assert "set CXX" in str(caught.value)


@pytest.mark.usefixtures("fresh_toolchain")
def test_entering_msvc_refuses_when_no_installation_has_the_cpp_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vswhere = _cmake.vswhere_path({"PROGRAMFILES(X86)": str(tmp_path / "pf")})
    vswhere.parent.mkdir(parents=True)
    vswhere.write_text("")
    monkeypatch.setattr(_cmake, "_on_windows", lambda: True)
    monkeypatch.setattr(platform, "machine", lambda: "AMD64")
    monkeypatch.delenv("CXX", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("PROGRAMFILES(X86)", str(tmp_path / "pf"))
    asked: list[list[str]] = []

    def _nothing(argv: list[str]) -> str:
        asked.append(argv)
        return ""

    monkeypatch.setattr(_cmake, "_asked", _nothing)
    with pytest.raises(_FAILURES, match="no Visual Studio installation has the"):
        _cmake.toolchain_env()
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
    vswhere = _cmake.vswhere_path({"PROGRAMFILES(X86)": str(tmp_path / "pf")})
    vswhere.parent.mkdir(parents=True)
    vswhere.write_text("")
    monkeypatch.setattr(_cmake, "_on_windows", lambda: True)
    monkeypatch.setattr(platform, "machine", lambda: "ARM64")
    monkeypatch.delenv("CXX", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("PROGRAMFILES(X86)", str(tmp_path / "pf"))
    monkeypatch.setattr(_cmake, "_asked", lambda argv: "C:\\VS")
    scripts: list[str] = []

    def _no_toolset(script: str) -> str:
        scripts.append(script)
        return "PATH=C:\\Windows\n"

    monkeypatch.setattr(_cmake, "_shell_set", _no_toolset)
    with pytest.raises(_FAILURES, match="left no VCToolsInstallDir"):
        _cmake.toolchain_env()
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
    vswhere = _cmake.vswhere_path({"PROGRAMFILES(X86)": str(tmp_path / "pf")})
    vswhere.parent.mkdir(parents=True)
    vswhere.write_text("")
    monkeypatch.setattr(_cmake, "_on_windows", lambda: True)
    monkeypatch.setattr(platform, "machine", lambda: "AMD64")
    monkeypatch.delenv("CXX", raising=False)
    monkeypatch.delenv("CC", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("PROGRAMFILES(X86)", str(tmp_path / "pf"))
    asked: list[list[str]] = []

    def _installation(argv: list[str]) -> str:
        asked.append(argv)
        return "C:\\VS"

    monkeypatch.setattr(_cmake, "_asked", _installation)
    monkeypatch.setattr(
        _cmake,
        "_shell_set",
        lambda script: (
            "=C:=C:\\work\n"
            "Path=C:\\VS\\VC\\bin;C:\\Windows\n"
            "VCToolsInstallDir=C:\\VS\\VC\\Tools\\MSVC\\14.51\\\n"
            "INCLUDE=C:\\VS\\VC\\include\n"
            "no equals sign here\n"
        ),
    )
    env = _cmake.toolchain_env()
    assert env["PATH"] == "C:\\VS\\VC\\bin;C:\\Windows"
    assert env["VCTOOLSINSTALLDIR"] == "C:\\VS\\VC\\Tools\\MSVC\\14.51\\"
    assert env["INCLUDE"] == "C:\\VS\\VC\\include"
    assert (env["CC"], env["CXX"]) == ("cl", "cl")
    assert not any(key.startswith("=") or key == "" for key in env)
    assert "no equals sign here" not in env
    # Entered once: the second call copies what the first read.
    again = _cmake.toolchain_env()
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
    import livery.toolroom.tools as tools

    package = _render_cpp(tmp_path)
    _listing_coverage(tmp_path)
    build_dir = package.directory / _cmake.GATE_BUILD_DIR
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
    monkeypatch.setattr(_cmake, "_ctest_program", lambda: "C:/store/ctest.exe")
    monkeypatch.setattr(_cmake, "toolchain_env", lambda: dict(os.environ))
    return package, exe


def _leftovers(exe: Path) -> list[str]:
    return sorted(p.name for p in exe.parent.glob("*instrumented*"))


def test_the_msvc_measurer_refuses_an_undeployed_engine_naming_the_requirement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    engine = _FakeEngine(junit=_GREEN, deployed=False)
    package, exe = _msvc_gate(tmp_path, monkeypatch, engine)
    with pytest.raises(_FAILURES, match="dotnet-coverage is not on PATH") as caught:
        _cmake.test(package, tmp_path)
    assert "cmake's [for.coverage] table requires it on Windows" in str(caught.value)
    assert "toolroom.lock" in str(caught.value)
    assert exe.read_bytes() == b"MZ original"
    assert _native_lines(tmp_path) == {}


def test_the_msvc_measurer_refuses_a_build_naming_no_test_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _FakeEngine(junit=_GREEN)
    package, exe = _msvc_gate(tmp_path, monkeypatch, engine)
    exe.unlink()
    with pytest.raises(_FAILURES, match="names no test executable to instrument"):
        _cmake.test(package, tmp_path)
    assert engine.calls == []


def test_the_msvc_measurer_refuses_an_instrumentation_that_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    engine = _FakeEngine(junit=_GREEN, instrument_code=1)
    package, exe = _msvc_gate(tmp_path, monkeypatch, engine)
    with pytest.raises(
        _FAILURES, match=r"could not instrument test_native.exe \(exit 1\)"
    ) as caught:
        _cmake.test(package, tmp_path)
    assert "no symbols" in str(caught.value)
    assert [call[0] for call in engine.calls] == ["instrument"]
    assert exe.read_bytes() == b"MZ original"
    assert _leftovers(exe) == []
    assert _native_lines(tmp_path) == {}


def test_a_red_ctest_under_the_engine_is_read_from_ctest_s_own_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The collector's exit code is its own: the verdict is ctest's JUnit report."""
    engine = _FakeEngine(junit=_RED, collect_code=0)
    package, exe = _msvc_gate(tmp_path, monkeypatch, engine)
    engine.report = _cobertura(package)
    with pytest.raises(_FAILURES, match=r"ctest failed \(1 of 2 test\(s\)\)") as caught:
        _cmake.test(package, tmp_path)
    assert "test_native ... Passed" in str(caught.value)
    # The instrumented copy ran in the original's place, and the
    # original is back whatever the verdict.
    assert engine.ran == b"MZ instrumented"
    assert exe.read_bytes() == b"MZ original"
    assert _leftovers(exe) == []
    assert _native_lines(tmp_path) == {}


def test_the_msvc_measurer_refuses_a_run_that_left_no_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _FakeEngine(junit=None, collect_code=1)
    package, exe = _msvc_gate(tmp_path, monkeypatch, engine)
    with pytest.raises(_FAILURES, match=r"ctest left no report at ctest\.xml"):
        _cmake.test(package, tmp_path)
    assert exe.read_bytes() == b"MZ original"


def test_a_selection_no_ctest_answers_to_is_a_refusal_under_the_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _FakeEngine(junit=_EMPTY)
    package, _exe = _msvc_gate(tmp_path, monkeypatch, engine)
    with pytest.raises(_FAILURES, match="no ctest is named test_missing"):
        _cmake.test(package, tmp_path, selection=("tests/test_missing.cpp",))
    collect = engine.calls[-1]
    assert collect[collect.index("-R") + 1] == "^(test_missing)$"
    engine.calls.clear()
    with pytest.raises(_FAILURES, match="ctest ran no test; register one"):
        _cmake.test(package, tmp_path)
    assert "-R" not in engine.calls[-1]


def test_the_msvc_measurer_refuses_a_collector_that_failed_after_a_green_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _FakeEngine(junit=_GREEN, collect_code=3)
    package, _exe = _msvc_gate(tmp_path, monkeypatch, engine)
    with pytest.raises(_FAILURES, match="dotnet-coverage exited 3 collecting"):
        _cmake.test(package, tmp_path)


def test_the_msvc_measurer_refuses_a_green_run_without_a_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _FakeEngine(junit=_GREEN)
    package, _exe = _msvc_gate(tmp_path, monkeypatch, engine)
    with pytest.raises(_FAILURES, match="dotnet-coverage wrote no report at"):
        _cmake.test(package, tmp_path)


def test_a_green_ctest_run_under_the_engine_is_measured_from_its_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:

    engine = _FakeEngine(junit=_GREEN)
    package, exe = _msvc_gate(tmp_path, monkeypatch, engine)
    engine.report = _cobertura(package)
    _cmake.test(package, tmp_path)
    assert _native_lines(tmp_path) == {
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
    _cmake.test(package, tmp_path)
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
    from livery.extensions.coverage import _floors as coverage_floors

    package = _render_cpp(tmp_path)
    _listing_coverage(tmp_path)
    _cmake.gate_build(package, tmp_path)
    compiler_id, compiler = _cmake.compiler_of(package)
    assert compiler_id == "MSVC", (compiler_id, compiler)
    _cmake.test(package, tmp_path)
    parts = _native_lines(tmp_path)
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
    # Microsoft's engine reads no columns: its part holds no ranges.
    from livery.extensions.coverage._parts import part_files, read_part

    (written,) = part_files(tmp_path)
    part = read_part(written, tmp_path)
    assert not part.measurer.columns
    assert not [entry for entry in part.files.values() if entry.ranges]
    measured = coverage_floors.measured_coverage(tmp_path, (package,))
    assert 0.0 < measured["packages/native"] <= 100.0
    assert "measured by msvc" in capsys.readouterr().out
    build_dir = package.directory / _cmake.GATE_BUILD_DIR
    assert (build_dir / "test_native.exe").is_file()
    assert not list(build_dir.rglob("*instrumented*"))
    # A red test fails the run with ctest's output, read from ctest's
    # own report under the collector.
    test_file = package.directory / "tests" / "test_native.cpp"
    test_file.write_text(test_file.read_text().replace("return 0;", "return 1;"))
    _cmake.gate_build(package, tmp_path)
    with pytest.raises(_FAILURES, match=r"ctest failed \(1 of 1 test"):
        _cmake.test(package, tmp_path)
