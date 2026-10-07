"""A package in a group directory: ``packages/<group>/<name>/``."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from livery.workshop import discover_packages, verify_workspace
from livery.workshop._contract import contract_paths
from livery.workshop._packages import (
    member_depth,
    member_of,
    package_directories,
    receipt_member,
)
from livery.workshop._pytest_layout import offenders
from livery.workshop._pytest_speed import package_of
from livery.workshop._release_driver import uncut_in_set
from livery.workshop._seeds import derived

_FAILURES = (BaseException,)


def _package(root: Path, member: str, *, contract_extra: str = "") -> Path:
    directory = root / "packages" / member
    directory.mkdir(parents=True)
    name = "livery-" + member.replace("/", "-")
    (directory / "workshop.toml").write_text(
        f'kind = "python"\nname = "{name}"\n{contract_extra}'
    )
    (directory / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\ndependencies = []\n'
    )
    return directory


def test_a_contractless_directory_in_a_group_is_refused_by_its_member_path(
    tmp_path: Path,
) -> None:
    _package(tmp_path, "extensions/ruff")
    (tmp_path / "packages" / "extensions" / "stray").mkdir()
    with pytest.raises(
        ValueError, match=re.escape("extensions/stray: no workshop.toml")
    ):
        discover_packages(tmp_path)


def test_groups_do_not_nest(tmp_path: Path) -> None:
    _package(tmp_path, "outer/inner/deep")
    with pytest.raises(ValueError, match=re.escape("outer: no workshop.toml")):
        discover_packages(tmp_path)


def test_a_package_inside_a_package_is_not_a_group_member(tmp_path: Path) -> None:
    _package(tmp_path, "forge")
    _package(tmp_path, "forge/inner")
    assert [p.member for p in discover_packages(tmp_path)] == ["forge"]


def test_a_grouped_package_is_found_by_its_path_in_path_order(
    tmp_path: Path,
) -> None:
    _package(tmp_path, "forge")
    _package(tmp_path, "extensions/ruff")
    _package(tmp_path, "extensions/mypy")
    _package(tmp_path, "zeta")
    packages = discover_packages(tmp_path)
    assert [p.path for p in packages] == [
        "packages/extensions/mypy",
        "packages/extensions/ruff",
        "packages/forge",
        "packages/zeta",
    ]
    assert packages[1].member == "extensions/ruff"
    assert packages[1].name == "livery-extensions-ruff"


def test_an_edge_to_a_grouped_package_passes_the_layering_lint(
    tmp_path: Path,
) -> None:
    _package(tmp_path, "extensions/ruff")
    _package(
        tmp_path,
        "tool",
        contract_extra=(
            '[[depends]]\npath = "packages/extensions/ruff"\nkind = "test"\n'
            'floor = "0.1.0"\n'
        ),
    )
    assert [p.member for p in verify_workspace(tmp_path)] == [
        "extensions/ruff",
        "tool",
    ]


def test_every_contract_is_listed_with_a_grouped_one(tmp_path: Path) -> None:
    (tmp_path / "workshop.toml").write_text("")
    _package(tmp_path, "forge")
    _package(tmp_path, "extensions/ruff")
    assert [p.relative_to(tmp_path).as_posix() for p in contract_paths(tmp_path)] == [
        "workshop.toml",
        "packages/extensions/ruff/workshop.toml",
        "packages/forge/workshop.toml",
    ]


def test_package_directories_without_a_packages_directory_is_empty(
    tmp_path: Path,
) -> None:
    assert package_directories(tmp_path) == ()


def test_a_path_splits_at_its_package_directory(tmp_path: Path) -> None:
    _package(tmp_path, "forge")
    _package(tmp_path, "extensions/ruff")
    assert member_depth(tmp_path, Path("packages/forge/src/x.py")) == 2
    assert member_depth(tmp_path, Path("packages/extensions/ruff/src/x.py")) == 3
    # The group directory itself, and paths outside packages/, are no
    # package's.
    assert member_depth(tmp_path, Path("packages/extensions/ruff")) == 0
    assert member_depth(tmp_path, Path("docs/index.md")) == 0
    assert member_depth(tmp_path, Path("packages/forge")) == 0


def test_the_longest_member_owns_a_path() -> None:
    members = ("extensions", "extensions/ruff")
    assert member_of("packages/extensions/ruff/src/x.py", members) == "extensions/ruff"
    assert member_of("packages/other/x.py", members) == ""


def test_a_receipt_names_its_member_whatever_its_depth() -> None:
    assert receipt_member("packages/forge/v1.2.0") == "forge"
    assert receipt_member("packages/extensions/ruff/v0.1.0") == "extensions/ruff"
    assert receipt_member("archive/setup") == ""
    assert receipt_member("v1.0.0") == ""


def test_an_uncut_grouped_receipt_belongs_to_its_set(tmp_path: Path) -> None:
    _package(tmp_path, "forge")
    _package(tmp_path, "extensions/ruff")
    ruff = next(p for p in discover_packages(tmp_path) if p.member != "forge")
    missing = ("packages/extensions/ruff/v0.2.0", "packages/forge/v1.0.0")
    assert uncut_in_set(missing, (ruff,)) == ("packages/extensions/ruff/v0.2.0",)


def test_a_test_node_names_its_package_at_either_depth() -> None:
    assert package_of("packages/forge/tests/test_x.py::test_y") == "packages/forge"
    assert (
        package_of("packages/extensions/ruff/tests/test_x.py::test_y")
        == "packages/extensions/ruff"
    )
    assert (
        package_of("packages/extensions/ruff/src/livery/x.py::x")
        == "packages/extensions/ruff"
    )
    assert package_of("tests/test_root.py::test_y") == ""


def test_a_grouped_helper_module_carries_the_group_in_its_prefix(
    tmp_path: Path,
) -> None:
    tests = _package(tmp_path, "extensions/ruff") / "tests"
    tests.mkdir()
    (tests / "ruff_seeds.py").write_text("")
    (tests / "extensions_ruff_ok.py").write_text("")
    assert offenders(tmp_path) == [
        (tests / "ruff_seeds.py", "extensions_ruff_ruff_seeds.py")
    ]


def test_each_hyphen_in_the_name_is_a_namespace_level() -> None:
    facts = {
        "namespace_package": "livery",
        "package_name": "livery-toolroom-store",
        "package_dir": "toolroom-store",
    }
    assert derived(facts)["import_path"] == "livery.toolroom.store"
    # Without a namespace the package keeps one top-level module.
    bare = {"package_name": "my-tool", "package_dir": "my-tool"}
    assert derived(bare) == {
        "project_slug": "my_tool",
        "source_path": "my_tool",
        "import_path": "my_tool",
    }


def test_a_grouped_package_imports_under_its_group() -> None:
    facts = {
        "namespace_package": "livery",
        "package_name": "livery-extensions-ruff",
        "package_dir": "extensions/ruff",
    }
    assert derived(facts) == {
        "project_slug": "ruff",
        "source_path": "livery/extensions/ruff",
        "import_path": "livery.extensions.ruff",
    }
    flat = {**facts, "package_name": "livery-forge", "package_dir": "forge"}
    assert derived(flat)["import_path"] == "livery.forge"
