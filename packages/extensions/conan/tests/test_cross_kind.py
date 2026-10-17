"""A python member's dependency on a C++ library: agreement, affected, order.

The python member lists nanobind, which requires conan, so each test
that reads it skips while nanobind is not installed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from extensions_conan_workspace import skip_unless_installed
from livery import git
from livery.extensions.conan import _package as _conan
from livery.workshop._graph import (
    affected_packages,
    dependents_closure,
    order_topologically,
)
from livery.workshop._packages import Package, verify_workspace

_FAILURES = (BaseException,)


#: The line a member listing nanobind writes.
NANOBIND = 'extensions = ["nanobind"]\n'

#: The line the C++ library writes.
LIBRARY = 'extensions = ["cmake", "conan"]\n'


def _member(
    root: Path,
    name: str,
    *,
    contract_tail: str = "",
    files: dict[str, str] | None = None,
) -> Path:
    directory = root / "packages" / name
    directory.mkdir(parents=True)
    (directory / "workshop.toml").write_text(f'name = "acme-{name}"\n{contract_tail}')
    for filename, body in (files or {}).items():
        (directory / filename).write_text(body)
    return directory


def _library(root: Path, *, requires: str = "") -> Path:
    return _member(
        root,
        "geometry",
        contract_tail=LIBRARY,
        files={"conanfile.py": f'requires = ({requires})\nname = "acme-geometry"\n'},
    )


def _extension(root: Path, *, conan_ref: str, floor: str = "0.1.0") -> Path:
    tail = (
        f'[[depends]]\npath = "packages/geometry"\nkind = "build"\nfloor = "{floor}"\n'
    )
    files = {
        "pyproject.toml": '[project]\nname = "acme-ext"\ndependencies = []\n',
    }
    if conan_ref:
        files["conanfile.py"] = f'requires = "{conan_ref}"\n'
    return _member(root, "ext", contract_tail=NANOBIND + tail, files=files)


# The refusals first.


def test_disagreeing_floors_refuse_naming_both(tmp_path: Path) -> None:
    skip_unless_installed("nanobind")
    _library(tmp_path)
    _extension(tmp_path, conan_ref="acme-geometry/[>=0.1.0]", floor="0.2.0")
    with pytest.raises(ValueError) as caught:
        verify_workspace(tmp_path)
    message = str(caught.value)
    assert "floors at 0.2.0" in message
    assert "acme-geometry[>=0.1.0]" in message


def test_a_missing_conan_requirement_refuses_naming_the_sibling_s_set(
    tmp_path: Path,
) -> None:
    skip_unless_installed("cmake", "nanobind")
    _library(tmp_path)
    _extension(tmp_path, conan_ref="", floor="0.1.0")
    with pytest.raises(ValueError) as caught:
        verify_workspace(tmp_path)
    # The sibling's set says which ecosystem's manifest the requirement
    # belongs in.
    assert (
        "packages/ext: the build edge on packages/geometry is not declared in the"
        " package's manifests (acme-geometry missing; packages/geometry holds"
        " cmake, cpp, conan)"
    ) in str(caught.value)


def test_an_undeclared_internal_conan_require_refuses(tmp_path: Path) -> None:
    skip_unless_installed("nanobind")
    _library(tmp_path)
    # The extension requires the library natively but declares no edge.
    _member(
        tmp_path,
        "ext",
        contract_tail=NANOBIND,
        files={
            "pyproject.toml": '[project]\nname = "acme-ext"\ndependencies = []\n',
            "conanfile.py": 'requires = "acme-geometry/[>=0.1.0]"\n',
        },
    )
    with pytest.raises(ValueError, match=r"no\s+\[\[depends\]\] edge"):
        verify_workspace(tmp_path)


# Agreement holds, the graph crosses ecosystems, the order is stable.


def test_agreeing_floors_pass(tmp_path: Path) -> None:
    skip_unless_installed("nanobind")
    _library(tmp_path)
    _extension(tmp_path, conan_ref="acme-geometry/[>=0.1.0]", floor="0.1.0")
    packages = verify_workspace(tmp_path)
    assert [p.name for p in packages] == ["acme-ext", "acme-geometry"]


def test_touching_the_library_marks_the_extension(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skip_unless_installed("nanobind")
    _library(tmp_path)
    _extension(tmp_path, conan_ref="acme-geometry/[>=0.1.0]", floor="0.1.0")

    monkeypatch.setattr(
        git, "changed_paths", lambda root, base: ["packages/geometry/src/geometry.cpp"]
    )
    affected = affected_packages(tmp_path)
    assert affected is not None
    assert {p.name for p in affected} == {"acme-geometry", "acme-ext"}
    # And the closure alone answers the same across ecosystems.
    packages = tuple(sorted(affected, key=lambda p: p.path))
    closure = dependents_closure(packages, {"packages/geometry"})
    assert {p.name for p in closure} == {"acme-geometry", "acme-ext"}


def test_the_order_puts_the_library_first(tmp_path: Path) -> None:
    skip_unless_installed("nanobind")
    _library(tmp_path)
    _extension(tmp_path, conan_ref="acme-geometry/[>=0.1.0]", floor="0.1.0")
    packages = verify_workspace(tmp_path)
    ordered = order_topologically(packages)
    assert [p.name for p in ordered] == ["acme-geometry", "acme-ext"]


# The extractors themselves.


def test_conan_requirements_reads_every_declared_form(tmp_path: Path) -> None:
    recipe = tmp_path / "conanfile.py"
    recipe.write_text(
        'requires = "alpha/[>=1.0]"\n'
        "class Thing:\n"
        '    requires = ("beta/[>=2.0]", "gamma/3.1")\n'
        "    def requirements(self):\n"
        '        self.requires("delta/[>=4.0]")\n'
    )
    assert _conan.conan_requirements(recipe) == {
        "alpha": "[>=1.0]",
        "beta": "[>=2.0]",
        "gamma": "3.1",
        "delta": "[>=4.0]",
    }


def test_a_package_listing_nanobind_reads_both_ecosystems_requirements(
    tmp_path: Path,
) -> None:
    skip_unless_installed("nanobind")
    from livery.workshop import _lifecycle

    directory = _member(
        tmp_path,
        "ext",
        contract_tail=NANOBIND,
        files={
            "pyproject.toml": (
                '[project]\nname = "acme-ext"\ndependencies = ["acme-core>=0.3.0"]\n'
            ),
            "conanfile.py": 'requires = "acme-geometry/[>=0.1.0]"\n',
        },
    )
    package = Package(
        directory=directory,
        path="packages/ext",
        name="acme-ext",
        depends=(),
        extensions=("nanobind",),
    )
    # python's answer from the pyproject and conan's from the recipe.
    declared = _lifecycle.declared_requirements(package)
    assert declared is not None
    assert declared["acme-core"] == ">=0.3.0"
    assert declared["acme-geometry"] == "[>=0.1.0]"


def test_the_set_s_floors_move_the_conan_range_in_a_consumer_s_recipe(
    tmp_path: Path,
) -> None:
    from livery.workshop._release_driver import MemberPlan, bump_set_floors

    skip_unless_installed("nanobind")
    # The library names a reference, so it releases a version; the
    # python member's recipe is a consumer's, which names none.
    _member(
        tmp_path,
        "geometry",
        contract_tail=LIBRARY,
        files={
            "conanfile.py": (
                'requires = ()\nname = "acme-geometry"\nversion = "0.1.0"\n'
            )
        },
    )
    ext_dir = _extension(tmp_path, conan_ref="acme-geometry/[>=0.1.0]", floor="0.1.0")
    (ext_dir / "pyproject.toml").write_text(
        '[project]\nname = "acme-ext"\nversion = "0.4.0"\ndependencies = []\n'
    )
    packages = {p.name: p for p in verify_workspace(tmp_path)}
    plans = (
        MemberPlan(package=packages["acme-geometry"], version="0.2.0"),
        MemberPlan(package=packages["acme-ext"], version="0.5.0"),
    )
    changed = bump_set_floors(tmp_path, plans)
    # nanobind's stamp raises the recipe's range through conan, and the
    # base the contract's floor, so the lint stays green.
    assert sorted(changed) == [
        "packages/ext/conanfile.py",
        "packages/ext/workshop.toml",
    ]
    assert (
        'requires = "acme-geometry/[>=0.2.0]"' in (ext_dir / "conanfile.py").read_text()
    )
    assert 'floor = "0.2.0"' in (ext_dir / "workshop.toml").read_text()
    verify_workspace(tmp_path)


def test_conan_s_stamp_raises_a_floor_in_a_recipe_that_names_a_reference(
    tmp_path: Path,
) -> None:
    from livery.workshop import _lifecycle

    skip_unless_installed("cmake", "conan")
    _member(
        tmp_path,
        "geometry",
        contract_tail=LIBRARY,
        files={
            "conanfile.py": (
                'requires = ()\nname = "acme-geometry"\nversion = "0.1.0"\n'
            )
        },
    )
    shapes = _member(
        tmp_path,
        "shapes",
        contract_tail=LIBRARY
        + '[[depends]]\npath = "packages/geometry"\nkind = "runtime"\n'
        + 'floor = "0.1.0"\n',
        files={
            "conanfile.py": (
                'requires = ("acme-geometry/[>=0.1.0]",)\nname = "acme-shapes"\n'
                'version = "0.3.0"\n'
            )
        },
    )
    packages = {p.name: p for p in verify_workspace(tmp_path)}
    shaped = packages["acme-shapes"]
    # Released alone, the stamp moves no floor.
    assert _lifecycle.stamp(shaped, tmp_path, "0.3.0") == []
    changed = _lifecycle.stamp(
        shaped, tmp_path, "0.4.0", floors={"packages/geometry": "0.2.0"}
    )
    assert changed == ["conanfile.py", "workshop.toml"]
    recipe = (shapes / "conanfile.py").read_text()
    assert '"acme-geometry/[>=0.2.0]"' in recipe
    assert 'version = "0.4.0"' in recipe
    assert 'floor = "0.2.0"' in (shapes / "workshop.toml").read_text()
    verify_workspace(tmp_path)
    # A recipe that names none of the floors changes nothing for them.
    geometry = packages["acme-geometry"]
    assert (
        _lifecycle.stamp(
            geometry, tmp_path, "0.1.0", floors={"packages/shapes": "0.4.0"}
        )
        == []
    )
