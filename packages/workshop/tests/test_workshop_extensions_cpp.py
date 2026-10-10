"""The cmake, cpp and conan extensions in the wheel: what they declare and answer."""

from __future__ import annotations

import importlib.metadata
from pathlib import Path

import pytest

from livery.workshop import _lifecycle
from livery.workshop._packages import Package

#: A recipe with a version and one requirement, as a born library's has.
RECIPE = (
    "from conan import ConanFile\n\n\n"
    "class Recipe(ConanFile):\n"
    '    name = "acme-lib"\n'
    '    version = "1.0.0"\n'
    '    requires = ("acme-base/[>=0.1 <1]",)\n'
)


def _member(directory: Path, extensions: tuple[str, ...]) -> Package:
    """A ``cpp-conan`` member in *directory* listing *extensions*, with the recipe."""
    directory.mkdir(parents=True)
    (directory / "conanfile.py").write_text(RECIPE)
    return Package(
        directory=directory,
        path=f"packages/{directory.name}",
        name="acme-lib",
        kind="cpp-conan",
        depends=(),
        extensions=extensions,
    )


def test_the_wheel_ships_cmake_cpp_and_conan_as_package_level_extensions() -> None:
    from livery.workshop._extensions import declaration

    found = {
        entry.name: entry.value
        for entry in importlib.metadata.entry_points(group="workshop.extensions")
    }
    for name in ("cmake", "cpp", "conan"):
        assert found[name] == f"livery.extensions.{name}"
        declared = declaration(name)
        assert declared is not None
        assert declared.levels == ("package",)
    cmake = declaration("cmake")
    conan = declaration("conan")
    assert cmake is not None
    assert conan is not None
    # cmake names no language, and conan packages C and C++.
    assert (cmake.requires, cmake.compatible) == ((), ())
    assert (conan.requires, conan.compatible) == (("cpp",), ("cmake",))


def test_the_extensions_answer_a_cpp_conan_package_as_the_kind_backend_does(
    tmp_path: Path,
) -> None:
    """A member listing cmake and conan is answered as its twin listing nothing."""
    from livery.workshop._queries import (
        CURRENT_VERSION,
        MODULE_ROOTS,
        PUBLIC_MODULES,
        REQUIREMENTS,
        VERSION_FILES,
        answer,
    )

    listing = _member(tmp_path / "listing", ("cmake", "conan"))
    plain = _member(tmp_path / "plain", ())
    assert answer(listing, CURRENT_VERSION) == _lifecycle.current_version(plain)
    assert _lifecycle.current_version(plain) == "1.0.0"
    assert answer(listing, VERSION_FILES) == (listing.directory / "conanfile.py",)
    assert _lifecycle.version_files(plain) == (plain.directory / "conanfile.py",)
    requirements = answer(listing, REQUIREMENTS)
    assert dict(requirements) == _lifecycle.declared_requirements(plain)
    assert dict(requirements) == {"acme-base": "[>=0.1 <1]"}
    assert answer(listing, MODULE_ROOTS) == _lifecycle.module_roots(plain) == ()
    assert answer(listing, PUBLIC_MODULES) == _lifecycle.public_modules(plain) == ()


def test_the_conan_steps_stamp_build_and_publish_as_the_kind_backend_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One member through conan's steps, its twin through the backend."""
    from livery.extensions.conan import _package
    from livery.workshop._registries import RegistryTarget

    listing = _member(tmp_path / "listing", ("cmake", "conan"))
    plain = _member(tmp_path / "plain", ())
    stamped = _lifecycle.stamp(listing, tmp_path, "2.0.0")
    assert stamped == _lifecycle.stamp(plain, tmp_path, "2.0.0") == ["conanfile.py"]
    assert (listing.directory / "conanfile.py").read_text() == (
        plain.directory / "conanfile.py"
    ).read_text()
    built: list[str] = []

    def _build(package: Package, root: Path, *, epoch: int = 0) -> Path:
        del root, epoch
        built.append(package.path)
        return package.directory / "build"

    # The step calls the extension's own build, which conan create runs.
    monkeypatch.setattr(_package, "build", _build)
    assert _lifecycle.build(listing, tmp_path, epoch=7) == listing.directory / "build"
    assert built == ["packages/listing"]
    uploads: list[tuple[str, str, str]] = []

    def _publish(
        package: Package, url: str, *, version: str, local: bool, token: str = ""
    ) -> bool:
        del url, local
        uploads.append((package.path, version, token))
        return True

    monkeypatch.setattr(_package, "publish", _publish)
    target = RegistryTarget(kind="conan", url="https://conan.example/remote", token="t")
    for package in (listing, plain):
        assert _lifecycle.publish(package, tmp_path, version="2.0.0", target=target)
    assert uploads == [
        ("packages/listing", "2.0.0", "t"),
        ("packages/plain", "2.0.0", "t"),
    ]
