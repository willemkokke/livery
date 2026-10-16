"""The cpp extension: its distribution, its declaration, its answers and the compilers.

The refusals first: a workspace whose packages hold no cpp names no
compiler, a host that lacks one names it, and a package that lists cpp
alone has no release. Then its own distribution, the kit, the
categories it gives a package's paths, the answers a C or C++ package
gives, and the dev wheels of the loop.
"""

from __future__ import annotations

import importlib.metadata
from pathlib import Path

import pytest

from livery.footman import Failed
from livery.workshop import _extensions
from livery.workshop._packages import Package
from livery.workshop.testing import CLAUSES, Clause

#: The root contract of a workspace here, which lists no extension.
ROOT_CONTRACT = '[workspace]\nname = "acme"\nnamespace = "acme"\n'


def skip_unless_installed(*extensions: str) -> None:
    """Skip the running test while an extension of *extensions* is not installed.

    The suite mounts extensions this distribution does not depend on:
    conan and nanobind, which require cpp, and cmake, which conan's
    template lists. A run of this wheel alone, as a release leg makes,
    installs none of them. The reason names each missing extension and
    the distribution that ships it.
    """
    missing = [
        name
        for name in dict.fromkeys(extensions)
        if _extensions.installed_declaration(name) is None
    ]
    if missing:
        named = ", ".join(
            f"{name} ({_extensions.distribution_of(name)})" for name in missing
        )
        pytest.skip(f"not installed here: {named}")


def _workspace(root: Path, members: dict[str, str]) -> Path:
    """A workspace at *root* with a member of each contract, by its directory."""
    root.mkdir(parents=True)
    (root / "workshop.toml").write_text(ROOT_CONTRACT)
    for member, contract in members.items():
        (root / "packages" / member).mkdir(parents=True)
        (root / "packages" / member / "workshop.toml").write_text(contract)
    return root


def _package(directory: Path, name: str, *extensions: str) -> Package:
    """The package in *directory* named *name*, listing *extensions*."""
    return Package(
        directory=directory,
        path=f"packages/{directory.name}",
        name=name,
        depends=(),
        extensions=extensions,
    )


def _mount(root: Path) -> tuple[str, ...]:
    """Mount what *root*'s lists hold, as a verb there would; the names mounted."""
    from livery.footman import _registry as registry
    from livery.workshop._extensions import mount_extensions

    with registry.capture():
        return mount_extensions(root)


def _compiler_findings(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> list[tuple[str, str, str]]:
    """The host doctor's findings on compilers in *root*: what, found and fix."""
    from livery.workshop import _doctor

    monkeypatch.setattr(_doctor, "workspace_root", lambda start=None: root)
    return [
        (finding.what, finding.found, finding.fix)
        for finding in _doctor.host()
        if finding.what.startswith("host:")
    ]


# --- the refusals ---------------------------------------------------------------


def test_a_workspace_without_cpp_names_no_compiler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._env_tasks import missing_host_tools

    # A host on which no compiler resolves.
    monkeypatch.setattr("shutil.which", lambda *_args, **_kwargs: None)
    assert missing_host_tools(_workspace(tmp_path / "bare", {})) == ()
    root = _workspace(
        tmp_path / "acme",
        {
            # python answers no host tool, and a member listing nothing
            # holds no extension to answer one.
            "echo": 'name = "acme-echo"\nextensions = ["python"]\n',
            "plain": 'name = "acme-plain"\n',
        },
    )
    # The member holding python carries a pyproject.toml.
    (root / "packages" / "echo" / "pyproject.toml").write_text(
        '[project]\nname = "acme-echo"\n'
    )
    assert missing_host_tools(root) == ()
    assert _compiler_findings(root, monkeypatch) == [
        ("host: every compiler the workspace's packages need is present", "", "")
    ]


def test_a_missing_compiler_is_named_for_a_package_holding_cpp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from livery.workshop import _env_tasks
    from livery.workshop._packages import discover_packages
    from livery.workshop._queries import HOST_TOOLS, answer

    root = _workspace(
        tmp_path / "acme", {"lib": 'name = "acme-lib"\nextensions = ["cpp"]\n'}
    )
    assert {
        package.member: answer(package, HOST_TOOLS)
        for package in discover_packages(root)
    } == {"lib": ("cc", "c++")}
    # A host that resolves cc and lacks c++: c++ is named, once.
    monkeypatch.setattr(
        "shutil.which",
        lambda tool, *_args, **_kwargs: None if tool == "c++" else f"/usr/bin/{tool}",
    )
    assert _env_tasks.missing_host_tools(root) == ("c++",)
    assert _compiler_findings(root, monkeypatch) == [
        (
            "host: c++",
            "MISSING, a compiler the workspace's packages need",
            "install the platform toolchain",
        )
    ]
    # fm env.check names it among the problems that make it red.
    monkeypatch.setattr(
        "livery.workshop._extensions.workspace_root", lambda start=None: root
    )
    monkeypatch.setattr(_env_tasks, "tool_profile", lambda _root: ())
    assert _env_tasks.env_check() == 1
    assert "  c++: MISSING (a host compiler; " in capsys.readouterr().out


def test_a_package_listing_conan_holds_cpp_and_needs_its_compilers(
    tmp_path: Path,
) -> None:
    from livery.workshop._packages import discover_packages
    from livery.workshop._queries import HOST_TOOLS, answer

    skip_unless_installed("conan")
    root = _workspace(
        tmp_path / "acme",
        {"recipe": 'name = "acme-recipe"\nextensions = ["conan"]\n'},
    )
    (package,) = discover_packages(root)
    assert answer(package, HOST_TOOLS) == ("cc", "c++")


def test_a_package_listing_cpp_alone_has_no_release(tmp_path: Path) -> None:
    from livery.workshop import _lifecycle, build_release
    from livery.workshop._registries import RegistryTarget

    # cpp names a package's files and adds no step to any phase, so the
    # release train has nothing to run for a package that holds it alone.
    package = _package(tmp_path / "packages" / "lib", "acme-lib", "cpp")
    runs = {
        "build": lambda: build_release(package, tmp_path),
        "stamp": lambda: _lifecycle.stamp(package, tmp_path, "1.0.0"),
        "publish": lambda: _lifecycle.publish(
            package,
            tmp_path,
            version="1.0.0",
            target=RegistryTarget(kind="python", url="idx"),
        ),
        "verify": lambda: _lifecycle.verify(
            package,
            tmp_path,
            tree=tmp_path,
            version="1.0.0",
            python="3.14",
            index="",
            extras="",
        ),
    }
    for phase, run in runs.items():
        with pytest.raises(Failed) as refused:
            run()
        assert str(refused.value) == (
            f"packages/lib: its extensions (cpp) add no step to the {phase} phase,"
            " so the release has nothing to run there. List the extension whose"
            f" steps serve the {phase} phase"
        )
    # Nor does it read a version from the package's manifests.
    with pytest.raises(Failed) as refused:
        _lifecycle.current_version(package)
    assert str(refused.value) == (
        "packages/lib: its extensions (cpp) answer no current-version: none of"
        " them reads a version from the package's manifests, so the release"
        " train has no version to read or stamp. List the extension whose"
        " manifest carries the version"
    )


# --- its own distribution -----------------------------------------------------


def test_its_own_distribution_ships_cpp_and_the_workshop_wheel_none() -> None:
    from livery.workshop._extensions import declaration, shipping_distribution

    ours = {
        (entry.group, entry.name, entry.value)
        for entry in importlib.metadata.distribution(
            "livery-extensions-cpp"
        ).entry_points
    }
    assert ours == {("workshop.extensions", "cpp", "livery.extensions.cpp")}
    theirs = importlib.metadata.distribution("livery-workshop").entry_points
    assert [
        entry.name
        for entry in theirs
        if entry.value.startswith("livery.extensions.cpp")
    ] == []
    shipped = shipping_distribution("livery.extensions.cpp")
    assert shipped is not None and shipped.name == "livery-extensions-cpp"
    # At runtime it requires the workshop, whose queries it answers, and
    # nothing else; its suite reads footman beside it.
    required = importlib.metadata.requires("livery-extensions-cpp") or []
    assert required == [
        "livery-workshop>=0.10.0",
        "livery-footman>=0.60.0 ; extra == 'test'",
    ]
    declared = declaration("cpp")
    assert declared is not None
    assert declared.requires == ()
    assert declared.plugin == ""


def test_a_package_listing_cpp_mounts_it(tmp_path: Path) -> None:
    (tmp_path / "workshop.toml").write_text('[workspace]\nextensions = ["cpp"]\n')
    assert _mount(tmp_path) == ("cpp",)
    # With python beside it, in composition order, whatever the list's.
    (tmp_path / "workshop.toml").write_text(
        '[workspace]\nextensions = ["python", "cpp"]\n'
    )
    assert _mount(tmp_path) == ("cpp", "python")


@pytest.mark.parametrize("clause", CLAUSES, ids=lambda clause: clause.name)
def test_cpp_conforms(clause: Clause, tmp_path: Path) -> None:
    from livery.workshop._steps import StepRecord, steps_by_name
    from livery.workshop.testing import Subject

    (tmp_path / "workshop.toml").write_text('[workspace]\nextensions = ["cpp"]\n')
    _mount(tmp_path)
    # cpp registers no step: it answers queries and gives a package's
    # paths their categories.
    steps: tuple[StepRecord, ...] = tuple(
        record for record in steps_by_name().values() if record.extension == "cpp"
    )
    assert steps == ()
    subject = Subject("livery.extensions.cpp", sets=(("cpp",),))
    assert clause.judge(subject) == []


# --- a package's files ------------------------------------------------------------


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

    package = _package(tmp_path / "packages" / "native", "acme-native", "cpp")

    def category(path: str) -> str:
        return category_of(package, path).name

    assert category("tests/test_native.cpp") == TEST
    assert category("tests/geometry_test.c") == TEST
    assert category("tests/helpers.hpp") == TEST_SUPPORT
    assert category("tests/CMakeLists.txt") == TEST_SUPPORT
    assert category("src/native.cpp") == SOURCE
    assert category("include/acme/native.hpp") == SOURCE
    # The base's own table calls the rest configuration.
    assert category("CMakeLists.txt") == CONFIGURATION
    assert category("conanfile.py") == CONFIGURATION
    assert category_of(package, "src/native.cpp").supplier == "cpp"


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


# --- the answers ----------------------------------------------------------------


def test_a_c_or_cpp_package_names_no_module(tmp_path: Path) -> None:
    from livery.workshop import _lifecycle
    from livery.workshop._queries import PUBLIC_MODULES, answer

    # Other packages reach C or C++ code through headers and libraries,
    # never an import path.
    package = _package(tmp_path / "packages" / "lib", "acme-lib", "cpp")
    assert _lifecycle.module_roots(package) == ()
    assert answer(package, PUBLIC_MODULES) == ()
    assert _lifecycle.references(package) == ()


# --- the loop -------------------------------------------------------------------


def test_the_loop_s_dev_wheels_hold_this_distribution() -> None:
    # The loop's plain pass creates loop-cpp from cpp-package, which
    # lists conan, and loop-native from nanobind-module, which lists
    # nanobind; both require cpp. The dev wheels the pass builds and
    # publishes before the birth hold this distribution, so the newborn
    # installs it beside theirs.
    from livery.workshop._composition import closure
    from livery.workshop._e2e import LOOP_MEMBERS, dev_members, loop_stack
    from livery.workshop._extensions import installed_declaration

    # The loop's members need cmake, conan and nanobind, so the stack is
    # read where all three are installed.
    skip_unless_installed("cmake", "conan", "nanobind")
    root = Path(__file__).resolve().parents[4]
    templates = {member.name: member.template for member in LOOP_MEMBERS}
    assert templates["loop-cpp"] == "cpp-package"
    assert templates["loop-native"] == "nanobind-module"
    # The canonical list leaves cpp out, since conan and nanobind
    # require it, and the stack's set holds it.
    assert "cpp" not in loop_stack()
    assert "cpp" in closure(loop_stack(), installed_declaration)
    assert "extensions/cpp" in dev_members(root, loop_stack())
