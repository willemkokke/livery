"""The affected engine: seeds, closures, and the everything fallback."""

from __future__ import annotations

import subprocess
from pathlib import Path

from livery.workshop._git_ops import GitOps
from livery.workshop._graph import affected_packages, dependents_closure
from livery.workshop._packages import discover_packages


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=True)


def _workspace(tmp_path: Path) -> Path:
    origin = tmp_path / "origin.git"
    origin.mkdir()
    _git(origin, "init", "--bare", "--initial-branch=main")
    root = tmp_path / "ws"
    _git(tmp_path, "clone", str(origin), "ws")
    _git(root, "config", "user.email", "t@livery.local")
    _git(root, "config", "user.name", "T")
    (root / "livery.toml").write_text("[workspace]\n")
    for name, extra in (
        ("core", ""),
        ("mid", '[[depends]]\npath = "packages/core"\nkind = "build"\n'),
        ("top", '[[depends]]\npath = "packages/mid"\nkind = "build"\n'),
        ("aside", ""),
    ):
        directory = root / "packages" / name
        directory.mkdir(parents=True)
        (directory / "livery.toml").write_text(
            f'type = "python"\nname = "livery-{name}"\n{extra}'
        )
        (directory / "pyproject.toml").write_text(
            f'[project]\nname = "livery-{name}"\ndependencies = []\n'
        )
        (directory / "thing.py").write_text("x = 1\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "chore: seed")
    _git(root, "push", "-u", "origin", "main")
    _git(root, "checkout", "-b", "feat/change")
    return root


def test_the_closure_follows_reversed_edges(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    packages = discover_packages(root)
    closure = dependents_closure(packages, {"packages/core"})
    assert [p.path for p in closure] == [
        "packages/core",
        "packages/mid",
        "packages/top",
    ]
    assert [p.path for p in dependents_closure(packages, {"packages/top"})] == [
        "packages/top"
    ]


def test_a_leaf_change_affects_only_its_closure(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    (root / "packages" / "mid" / "thing.py").write_text("x = 2\n")
    affected = affected_packages(root, GitOps(root))
    assert affected is not None
    assert [p.path for p in affected] == ["packages/mid", "packages/top"]


def test_a_root_change_affects_everything(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    (root / "livery.toml").write_text("[workspace]\n# touched\n")
    assert affected_packages(root, GitOps(root)) is None


def test_no_change_affects_nothing(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    affected = affected_packages(root, GitOps(root))
    assert affected == ()


def test_committed_and_uncommitted_changes_both_count(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    (root / "packages" / "aside" / "thing.py").write_text("x = 3\n")
    _git(root, "commit", "-am", "feat: aside moves")
    (root / "packages" / "core" / "thing.py").write_text("x = 4\n")
    affected = affected_packages(root, GitOps(root))
    assert affected is not None
    assert {p.path for p in affected} == {
        "packages/aside",
        "packages/core",
        "packages/mid",
        "packages/top",
    }
