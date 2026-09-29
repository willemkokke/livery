"""The cpp-conan kind: refusals and skips first, then the armed build."""

from __future__ import annotations

import shutil
from pathlib import Path

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
#: naming what is missing instead of failing mid-configure.
_TOOLCHAIN = ("cmake", "ninja", "cc", "c++")
_MISSING_TOOLS = tuple(tool for tool in _TOOLCHAIN if shutil.which(tool) is None)
needs_toolchain = pytest.mark.skipif(
    bool(_MISSING_TOOLS),
    reason=f"host toolchain incomplete: {', '.join(_MISSING_TOOLS)} missing",
)


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
    assert record_for_template("package-python-layer") is None
    # The build tools are the kind's; clang-format and clang-tidy ride
    # their check records, which is where the profile reads them.
    assert kind_for("cpp-conan").tools == ("cmake", "conan", "ninja")
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
