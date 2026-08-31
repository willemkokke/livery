"""The layering lint refuses each violation by name, on synthetic trees."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from livery.workshop import discover_packages, verify_workspace


def _package(
    root: Path,
    name: str,
    *,
    contract_extra: str = "",
    dependencies: tuple[str, ...] = (),
) -> None:
    directory = root / "packages" / name
    directory.mkdir(parents=True)
    (directory / "livery.toml").write_text(
        f'type = "python"\nname = "livery-{name}"\n{contract_extra}'
    )
    deps = ", ".join(f'"{d}"' for d in dependencies)
    (directory / "pyproject.toml").write_text(
        f'[project]\nname = "livery-{name}"\ndependencies = [{deps}]\n'
    )


def _forge_stub(root: Path) -> None:
    # The stdlib rule walks packages/forge/src; give the tree a clean one.
    src = root / "packages" / "forge" / "src"
    src.mkdir(parents=True)
    (src / "ok.py").write_text("import json\n")
    (root / "packages" / "forge" / "livery.toml").write_text(
        'type = "python"\nname = "livery-forge"\n'
    )
    (root / "packages" / "forge" / "pyproject.toml").write_text(
        '[project]\nname = "livery-forge"\ndependencies = []\n'
    )


def test_a_contractless_package_is_refused(tmp_path: Path) -> None:
    (tmp_path / "packages" / "stray").mkdir(parents=True)
    with pytest.raises(ValueError, match=re.escape("stray: no livery.toml")):
        discover_packages(tmp_path)


def test_an_undeclared_native_dependency_is_refused(tmp_path: Path) -> None:
    _forge_stub(tmp_path)
    _package(tmp_path, "tool", dependencies=("livery-forge>=0.1.0",))
    with pytest.raises(ValueError, match="no \\[\\[depends\\]\\] edge"):
        verify_workspace(tmp_path)


def test_a_declared_edge_missing_natively_is_refused(tmp_path: Path) -> None:
    _forge_stub(tmp_path)
    _package(
        tmp_path,
        "tool",
        contract_extra=(
            '[[depends]]\npath = "packages/forge"\nkind = "build"\nfloor = "0.1.0"\n'
        ),
    )
    with pytest.raises(ValueError, match="is not in"):
        verify_workspace(tmp_path)


def test_a_floor_disagreement_is_refused(tmp_path: Path) -> None:
    _forge_stub(tmp_path)
    _package(
        tmp_path,
        "tool",
        contract_extra=(
            '[[depends]]\npath = "packages/forge"\nkind = "build"\nfloor = "0.2.0"\n'
        ),
        dependencies=("livery-forge>=0.1.0",),
    )
    with pytest.raises(ValueError, match=re.escape("floors at 0.2.0")):
        verify_workspace(tmp_path)


def test_a_cycle_is_refused(tmp_path: Path) -> None:
    _forge_stub(tmp_path)
    _package(
        tmp_path,
        "a",
        contract_extra='[[depends]]\npath = "packages/b"\nkind = "build"\n',
        dependencies=("livery-b",),
    )
    _package(
        tmp_path,
        "b",
        contract_extra='[[depends]]\npath = "packages/a"\nkind = "build"\n',
        dependencies=("livery-a",),
    )
    with pytest.raises(ValueError, match="dependency cycle"):
        verify_workspace(tmp_path)


def test_a_forge_third_party_import_is_refused(tmp_path: Path) -> None:
    _forge_stub(tmp_path)
    src = tmp_path / "packages" / "forge" / "src"
    (src / "bad.py").write_text("import requests\n")
    with pytest.raises(ValueError, match="stdlib-only at import time"):
        verify_workspace(tmp_path)


def test_the_dev_plugin_may_import_footman_and_nothing_else(tmp_path: Path) -> None:
    _forge_stub(tmp_path)
    plugin_dir = tmp_path / "packages" / "forge" / "src" / "livery" / "forge" / "_dev"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "__init__.py").write_text("import footman\n")
    verify_workspace(tmp_path)
    (plugin_dir / "__init__.py").write_text("import footman\nimport requests\n")
    with pytest.raises(ValueError, match="stdlib-only at import time"):
        verify_workspace(tmp_path)


def test_a_clean_tree_passes(tmp_path: Path) -> None:
    _forge_stub(tmp_path)
    _package(
        tmp_path,
        "tool",
        contract_extra=(
            '[[depends]]\npath = "packages/forge"\nkind = "build"\nfloor = "0.1.0"\n'
        ),
        dependencies=("livery-forge>=0.1.0",),
    )
    packages = verify_workspace(tmp_path)
    assert [p.name for p in packages] == ["livery-forge", "livery-tool"]
