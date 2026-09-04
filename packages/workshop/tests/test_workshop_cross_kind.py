"""The cross-kind dependency: extractors, agreement, affected, order."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from livery.workshop._backends import _cpp_conan
from livery.workshop._git_ops import GitOps
from livery.workshop._graph import (
    affected_packages,
    dependents_closure,
    order_topologically,
)
from livery.workshop._kinds import kind_for, kind_names
from livery.workshop._packages import Package, verify_workspace

_FAILURES = (BaseException,)


def _member(
    root: Path,
    name: str,
    *,
    type_name: str,
    contract_tail: str = "",
    files: dict[str, str] | None = None,
) -> Path:
    directory = root / "packages" / name
    directory.mkdir(parents=True)
    (directory / "workshop.toml").write_text(
        f'type = "{type_name}"\nname = "acme-{name}"\n{contract_tail}'
    )
    for filename, body in (files or {}).items():
        (directory / filename).write_text(body)
    return directory


def _library(root: Path, *, requires: str = "") -> Path:
    return _member(
        root,
        "geometry",
        type_name="cpp-conan",
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
    return _member(
        root, "ext", type_name="python-nanobind", contract_tail=tail, files=files
    )


# The pin: every registered kind carries an extractor.


def test_every_kind_has_a_declared_requirements_extractor() -> None:
    missing = [
        name
        for name in kind_names()
        if not callable(getattr(kind_for(name).backend, "declared_requirements", None))
    ]
    assert missing == []


# The refusals first.


def test_disagreeing_floors_refuse_naming_both(tmp_path: Path) -> None:
    _library(tmp_path)
    _extension(tmp_path, conan_ref="acme-geometry/[>=0.1.0]", floor="0.2.0")
    with pytest.raises(ValueError) as caught:
        verify_workspace(tmp_path)
    message = str(caught.value)
    assert "floors at 0.2.0" in message
    assert "acme-geometry[>=0.1.0]" in message


def test_a_missing_conan_requirement_refuses_teaching_the_form(
    tmp_path: Path,
) -> None:
    _library(tmp_path)
    _extension(tmp_path, conan_ref="", floor="0.1.0")
    with pytest.raises(ValueError) as caught:
        verify_workspace(tmp_path)
    message = str(caught.value)
    assert "not declared in conanfile.py" in message
    assert "acme-geometry/[>=0.1.0]" in message


def test_an_undeclared_internal_conan_require_refuses(tmp_path: Path) -> None:
    _library(tmp_path)
    # The extension requires the library natively but declares no edge.
    _member(
        tmp_path,
        "ext",
        type_name="python-nanobind",
        files={
            "pyproject.toml": '[project]\nname = "acme-ext"\ndependencies = []\n',
            "conanfile.py": 'requires = "acme-geometry/[>=0.1.0]"\n',
        },
    )
    with pytest.raises(ValueError, match=r"no\s+\[\[depends\]\] edge"):
        verify_workspace(tmp_path)


def test_an_unextractable_kind_refuses_in_the_lint(tmp_path: Path) -> None:
    _member(tmp_path, "mystery", type_name="carrier-pigeon")
    with pytest.raises(ValueError, match="not a registered kind"):
        verify_workspace(tmp_path)


def test_affected_fails_open_on_an_unknown_kind(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _member(tmp_path, "mystery", type_name="carrier-pigeon")

    class _Git:
        def changed_paths(self, base: str) -> list[str]:
            return ["packages/mystery/file.txt"]

    assert affected_packages(tmp_path, cast("GitOps", _Git())) is None
    out = capsys.readouterr().out
    assert "carrier-pigeon" in out
    assert "failing open" in out


# Agreement holds, the graph crosses kinds, the order is stable.


def test_agreeing_floors_pass(tmp_path: Path) -> None:
    _library(tmp_path)
    _extension(tmp_path, conan_ref="acme-geometry/[>=0.1.0]", floor="0.1.0")
    packages = verify_workspace(tmp_path)
    assert [p.name for p in packages] == ["acme-ext", "acme-geometry"]


def test_touching_the_library_marks_the_extension(tmp_path: Path) -> None:
    _library(tmp_path)
    _extension(tmp_path, conan_ref="acme-geometry/[>=0.1.0]", floor="0.1.0")

    class _Git:
        def changed_paths(self, base: str) -> list[str]:
            return ["packages/geometry/src/geometry.cpp"]

    affected = affected_packages(tmp_path, cast("GitOps", _Git()))
    assert affected is not None
    assert {p.name for p in affected} == {"acme-geometry", "acme-ext"}
    # And the closure alone answers the same across kinds.
    packages = tuple(sorted(affected, key=lambda p: p.path))
    closure = dependents_closure(packages, {"packages/geometry"})
    assert {p.name for p in closure} == {"acme-geometry", "acme-ext"}


def test_the_order_puts_the_library_first(tmp_path: Path) -> None:
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
    assert _cpp_conan.conan_requirements(recipe) == {
        "alpha": "[>=1.0]",
        "beta": "[>=2.0]",
        "gamma": "3.1",
        "delta": "[>=4.0]",
    }


def test_the_extension_extractor_unions_both_ecosystems(tmp_path: Path) -> None:
    directory = _member(
        tmp_path,
        "ext",
        type_name="python-nanobind",
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
        type="python-nanobind",
        depends=(),
    )
    from livery.workshop._backends import _python_nanobind

    declared = _python_nanobind.declared_requirements(package)
    assert declared["acme-core"] == ">=0.3.0"
    assert declared["acme-geometry"] == "[>=0.1.0]"


def test_bump_set_floors_moves_the_conan_range(tmp_path: Path) -> None:
    from livery.workshop._release_driver import MemberPlan, bump_set_floors

    _library(tmp_path)
    ext_dir = _extension(tmp_path, conan_ref="acme-geometry/[>=0.1.0]", floor="0.1.0")
    packages = {p.name: p for p in verify_workspace(tmp_path)}
    plans = (
        MemberPlan(package=packages["acme-geometry"], version="0.2.0"),
        MemberPlan(package=packages["acme-ext"], version="0.5.0"),
    )
    changed = bump_set_floors(tmp_path, plans)
    assert "packages/ext/conanfile.py" in changed
    assert (
        'requires = "acme-geometry/[>=0.2.0]"' in (ext_dir / "conanfile.py").read_text()
    )
    # The contract floor moved with it, so the lint stays green.
    assert 'floor = "0.2.0"' in (ext_dir / "workshop.toml").read_text()
    verify_workspace(tmp_path)
