"""The cpp-conan kind: refusals and skips first, then the armed build.

The cmake extension's own suite covers its gate build: its steps, the
configure, compile and ctest, the measured run and the MSVC measurer.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

from livery.extensions.conan import _package as _conan
from livery.footman import prog
from livery.workshop._packages import Package
from livery.workshop._phases import PhaseContext
from livery.workshop._steps import judges, step_for
from workshop_cmake_declared import cmake_steps_fixture  # noqa: F401
from workshop_installed import skip_unless_installed
from workshop_python_steps import python_steps_fixture  # noqa: F401

_FAILURES = (BaseException,)


#: The armed leg needs the host toolchain; a machine without it skips
#: naming what is missing instead of failing mid-configure. Windows
#: builds with MSVC, whose leg is the cmake extension's suite.
_TOOLCHAIN = ("cmake", "ninja", "cc", "c++")
_MISSING_TOOLS = tuple(tool for tool in _TOOLCHAIN if shutil.which(tool) is None)
needs_toolchain = pytest.mark.skipif(
    bool(_MISSING_TOOLS) or sys.platform == "win32",
    reason=f"host toolchain incomplete: {', '.join(_MISSING_TOOLS)} missing"
    if _MISSING_TOOLS
    else "the Windows leg builds with MSVC; the cmake extension's suite covers it",
)


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


def _render_cpp(tmp_path: Path) -> Package:
    """A cpp-conan package, straight from its seeds.

    conan's template lists cmake, so the test skips while cmake is not
    installed.
    """
    skip_unless_installed("cmake")
    destination = tmp_path / "packages" / "native"
    from workshop_composed import seed_into

    seed_into(
        destination,
        "cpp-library",
        {
            "package_name": "acme-native",
            "package_description": "acme-native: a native library.",
            "namespace_package": "acme",
            "project_name": "acme",
        },
    )
    # The native configs come from the step records, as they do at a
    # birth: the template ships none.
    from livery.workshop._composition import closure
    from livery.workshop._extensions import installed_declaration
    from livery.workshop._shipped_files import settle_package

    settle_package(destination, closure(("cmake", "conan"), installed_declaration))
    return _package(destination, "acme-native", "cmake", "conan")


# The refusals and the skips first.


def test_build_refuses_without_conan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A handle spawns by name, so an undeployed tool is a sentence.

    The spawn raises OSError instead of answering with a failing
    result, and a traceback names nothing a person can act on. The
    sentence is the workshop's refusal for a tool of the store not on
    PATH: it names the tool and the next action.
    """
    empty = tmp_path / "empty-path"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    package = _package(
        tmp_path / "packages" / "native", "acme-native", "cmake", "conan"
    )
    package.directory.mkdir(parents=True)
    with pytest.raises(_FAILURES) as caught:
        _conan.build(package, tmp_path)
    refusal = str(caught.value)
    assert refusal == _undeployed("conan")
    assert refusal.startswith("conan is not on PATH: it is a tool of the store")
    assert f"(`{prog()} sync`, then the printed env.emit line) and re-run" in refusal


class _Said:
    """What a faked conan call answers."""

    def __init__(self, code: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.code = code
        self.stdout = stdout
        self.stderr = stderr


def _profile_conan(
    monkeypatch: pytest.MonkeyPatch,
    shown: list[dict[str, str]],
    *,
    detect: int = 0,
    show: int = 0,
) -> list[tuple[str, ...]]:
    """Fake conan's profile commands; each show answers the next settings.

    *detect* and *show* are the exit codes of those commands. Returns
    every call's arguments, in order; ``create`` succeeds.
    """
    calls: list[tuple[str, ...]] = []
    answers = iter(shown)
    # No profile file to read: every answer below comes from conan.
    monkeypatch.setenv("CONAN_HOME", str(Path(tempfile.mkdtemp())))

    def _fake(cwd: Path, *args: str, env: object = None) -> _Said:
        del cwd, env
        calls.append(args)
        if args[:2] == ("profile", "detect"):
            return _Said(detect, stderr="detect: no default profile written")
        if args[:2] == ("profile", "show"):
            if show:
                return _Said(show, stderr="show: the profile does not parse")
            settings = next(answers)
            return _Said(stdout=json.dumps({"host": {"settings": settings}}))
        assert args == ("create", ".")
        return _Said()

    monkeypatch.setattr(_conan, "_conan", _fake)
    return calls


_COMPILERLESS = {"arch": "armv8", "build_type": "Release", "os": "Macos"}
_DETECTED = {**_COMPILERLESS, "compiler": "apple-clang", "compiler.version": "21"}
_EXIST_OK = ("profile", "detect", "--exist-ok")
_FORCE = ("profile", "detect", "--force")
_SHOW = ("profile", "show", "--format=json")


def test_a_machine_where_detection_finds_no_compiler_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _profile_conan(monkeypatch, [_COMPILERLESS, _COMPILERLESS])
    with pytest.raises(_FAILURES, match="found none on this machine: install a C"):
        _conan.ensure_profile(tmp_path)
    assert calls == [_EXIST_OK, _SHOW, _FORCE, _SHOW]


def test_a_detection_conan_refuses_is_named_in_its_words(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _profile_conan(monkeypatch, [], detect=1)
    with pytest.raises(_FAILURES) as caught:
        _conan.ensure_profile(tmp_path)
    assert "conan profile detect exited 1" in str(caught.value)
    assert "no default profile written" in str(caught.value)


def test_a_profile_conan_cannot_show_is_named_and_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A profile that does not parse is the person's to mend: nothing
    # replaces it.
    calls = _profile_conan(monkeypatch, [], show=1)
    with pytest.raises(_FAILURES) as caught:
        _conan.ensure_profile(tmp_path)
    assert "conan profile show exited 1" in str(caught.value)
    assert "the profile does not parse" in str(caught.value)
    assert _FORCE not in calls


def test_two_calls_at_once_detect_the_profile_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Two conan builds side by side each make the profile first. The
    # second call waits for the first, finds the profile it made, and
    # starts no conan: two detections against one conan home race.
    import threading

    home = tmp_path / "conan-home"
    monkeypatch.setenv("CONAN_HOME", str(home))
    profile = home / "profiles" / "default"
    first = threading.current_thread()
    calls: list[tuple[str, ...]] = []
    reached = threading.Event()
    second: list[threading.Thread] = []
    answered: list[list[str]] = []

    def _second_call() -> None:
        answered.append(_conan.ensure_profile(tmp_path))

    def _fake(cwd: Path, *args: str, env: object = None) -> _Said:
        del cwd, env
        calls.append(args)
        if threading.current_thread() is not first:
            reached.set()
        if args == _SHOW:
            return _Said(stdout=json.dumps({"host": {"settings": _DETECTED}}))
        assert args == _EXIST_OK
        if not second:
            # The second call starts while the first detects, and has
            # long enough to reach conan, were it let through.
            thread = threading.Thread(target=_second_call)
            second.append(thread)
            thread.start()
            reached.wait(0.2)
        profile.parent.mkdir(parents=True, exist_ok=True)
        profile.write_text("[settings]\ncompiler=apple-clang\ncompiler.version=21\n")
        return _Said()

    monkeypatch.setattr(_conan, "_conan", _fake)
    assert _conan.ensure_profile(tmp_path) == []
    second[0].join()
    assert not reached.is_set()
    assert answered == [[]]
    assert calls == [_EXIST_OK, _SHOW]


def test_a_profile_that_names_no_compiler_is_detected_again_and_said(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _profile_conan(monkeypatch, [_COMPILERLESS, _DETECTED])
    assert _conan.ensure_profile(tmp_path) == [
        "  conan: the default profile named no compiler; detected again: apple-clang 21"
    ]
    assert calls == [_EXIST_OK, _SHOW, _FORCE, _SHOW]


def test_a_profile_that_names_a_compiler_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _profile_conan(monkeypatch, [_DETECTED])
    assert _conan.ensure_profile(tmp_path) == []
    assert calls == [_EXIST_OK, _SHOW]


def test_a_profile_file_that_names_a_compiler_costs_no_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The prepare runs before every install on every job; a profile
    # already detected is read from its file and conan never starts.
    from livery.extensions.conan import _package

    calls = _profile_conan(monkeypatch, [])
    home = tmp_path / "conan-home"
    monkeypatch.setenv("CONAN_HOME", str(home))
    profile = home / "profiles" / "default"
    assert _package.profile_path(os.environ) == profile
    # Missing: conan is asked, as the fallbacks above pin.
    assert not _package.profile_names_compiler(profile)
    profile.parent.mkdir(parents=True)
    # Naming no compiler itself (an include may): conan is asked.
    profile.write_text("include(base)\n[settings]\narch=armv8\nos=Macos\n")
    assert not _package.profile_names_compiler(profile)
    profile.write_text("[settings]\ncompiler=\n[options]\ncompiler=x\n")
    assert not _package.profile_names_compiler(profile)
    # Naming one: nothing to ask.
    profile.write_text(
        "[settings]\narch=armv8\ncompiler=apple-clang\ncompiler.version=21\n"
    )
    assert _package.profile_names_compiler(profile)
    assert _conan.ensure_profile(tmp_path) == []
    assert calls == []
    # Without CONAN_HOME the file is conan's own default.
    monkeypatch.delenv("CONAN_HOME")
    own = Path.home() / ".conan2" / "profiles" / "default"
    assert _package.profile_path(os.environ) == own


def test_conan_create_runs_once_the_profile_names_a_compiler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = _profile_conan(monkeypatch, [_COMPILERLESS, _DETECTED])
    package = _package(
        tmp_path / "packages" / "native", "acme-native", "cmake", "conan"
    )
    package.directory.mkdir(parents=True)
    assert _conan.build(package, tmp_path) == package.directory / "build"
    assert calls == [_EXIST_OK, _SHOW, _FORCE, _SHOW, ("create", ".")]
    assert "detected again: apple-clang 21" in capsys.readouterr().out


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


def test_no_rendered_python_line_of_a_cpp_member_is_over_the_column_limit(
    tmp_path: Path,
) -> None:
    package = _render_cpp(tmp_path)
    over = [
        (path.name, line)
        for path in package.directory.rglob("*.py")
        for line in path.read_text("utf-8").splitlines()
        if len(line) > 88
    ]
    assert over == []


def test_the_cpp_template_seeds_no_floor_and_the_default_judges_it(
    tmp_path: Path,
) -> None:
    """A native member is judged like the others: at the default floor, 100."""
    from livery.extensions.coverage import _floors as coverage_floors
    from livery.workshop._contract import load_contract

    package = _render_cpp(tmp_path)
    assert "coverage" not in load_contract(package.directory / "workshop.toml")
    assert coverage_floors.coverage_policy(package) == coverage_floors.FloorPolicy(
        100.0, False, 0.5, default=True
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


def test_a_package_listing_cmake_and_conan_is_categorised_by_cpp_s_table(
    tmp_path: Path,
) -> None:
    """Conan requires cpp, so a package listing cmake and conan reads cpp's table."""
    from livery.workshop._categories import category_of

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


def test_the_conan_workspace_steps_aside_for_a_block_and_comes_back_after_a_failure(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / _conan.WORKSPACE_FILE
    aside = tmp_path / f"{_conan.WORKSPACE_FILE}.aside"
    # No workspace file: the block runs and nothing moves.
    with _conan.workspace_aside(tmp_path):
        assert not workspace.exists()
    assert not workspace.exists() and not aside.exists()
    workspace.write_text("packages: []\n")
    # A block that fails still puts the file back.

    def failing_leg() -> None:
        with _conan.workspace_aside(tmp_path):
            assert not workspace.exists()
            raise RuntimeError("leg failed")

    with pytest.raises(RuntimeError, match="leg failed"):
        failing_leg()
    assert workspace.read_text() == "packages: []\n" and not aside.exists()
    # A leg killed inside its block left the file aside, and a sync
    # wrote it again: the stale copy gives way to the current one.
    aside.write_text("stale\n")
    with _conan.workspace_aside(tmp_path):
        assert not workspace.exists()
        assert aside.read_text() == "packages: []\n"
    assert workspace.read_text() == "packages: []\n" and not aside.exists()


# The seed chain and the tools.


def test_the_cpp_template_extends_no_other(cmake_steps: object) -> None:
    # No python template comes before it; the package-base seeds still
    # come first, carrying the docs seeds.
    from livery.workshop._seeds import template_chain

    assert [template.name for template in template_chain("cpp-library")] == [
        "cpp-library"
    ]
    # The build tools come from the [toolroom] tables of the extensions
    # a package born from the seeds holds, cmake's and conan's, and the
    # measurer of a build made with MSVC from cmake's [for.coverage]
    # table; of the steps those extensions hold, cmake's configure and
    # compile name cmake, the tool they run.
    from livery.workshop._steps import tools_for

    assert tools_for(("cmake", "conan", "cpp")) == (
        ("cmake", "build.configure"),
        ("cmake", "build.compile"),
    )


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


# The armed leg: the fixture builds and its ctest passes.


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
