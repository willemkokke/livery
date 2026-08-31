"""The release train and the update wave, on synthetic trees.

verify/prepare run against a scratch workspace with real git tags;
the snapshot publisher runs against a local bare artifact repository,
so the refusal and the idempotency are proven without a network.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from footman import Failed

from livery.workshop._git_ops import GitOps
from livery.workshop._release import (
    prepare_release,
    publish_templates,
    verify_release,
)
from livery.workshop._update import bump_floors, latest_released

_FAILURES = (SystemExit, Failed)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


def _workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    root.mkdir()
    (root / "livery.toml").write_text("[workspace]\n")
    _git(root, "init", "--initial-branch=main")
    _git(root, "config", "user.email", "test@livery.local")
    _git(root, "config", "user.name", "Livery Test")
    for name, extra, deps in (
        ("core", "", ""),
        (
            "tool",
            '[[depends]]\npath = "packages/core"\nkind = "build"\nfloor = "0.1.0"\n',
            '"livery-core>=0.1.0"',
        ),
    ):
        directory = root / "packages" / name
        (directory / "src" / "livery" / name).mkdir(parents=True)
        (directory / "livery.toml").write_text(
            f'type = "python"\nname = "livery-{name}"\n{extra}'
        )
        (directory / "pyproject.toml").write_text(
            f'[project]\nname = "livery-{name}"\nversion = "0.2.0"\n'
            f"dependencies = [{deps}]\n"
        )
        (directory / "CHANGELOG.md").write_text("# Changelog\n\n## 0.2.0\n\n- x\n")
        (directory / "src" / "livery" / name / "__init__.py").write_text(
            '__version__ = "0.2.0"\n'
        )
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "chore: seed")
    _git(root, "tag", "packages/core/v0.1.0")
    return root


def test_verify_passes_a_release_shaped_tree(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    plan = verify_release(root, "packages/tool/v0.2.0")
    assert plan.package.name == "livery-tool" and plan.version == "0.2.0"


def test_verify_lists_every_disagreement(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    changelog = root / "packages" / "tool" / "CHANGELOG.md"
    changelog.write_text("# Changelog\n\n## 0.1.9\n\n- old\n")
    with pytest.raises(_FAILURES) as caught:
        verify_release(root, "packages/tool/v0.2.0")
    assert "CHANGELOG.md has no '## 0.2.0' entry" in str(caught.value)


def test_verify_refuses_an_unreleased_floor(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    contract = root / "packages" / "tool" / "livery.toml"
    contract.write_text(contract.read_text().replace("0.1.0", "0.9.9"))
    with pytest.raises(_FAILURES) as caught:
        verify_release(root, "packages/tool/v0.2.0")
    assert "floors must name released versions" in str(caught.value)


def test_prepare_stamps_idempotently(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    changed = prepare_release(root, "packages/tool", "0.3.0")
    assert "pyproject.toml" in changed
    assert any("CHANGELOG" in name for name in changed)
    assert prepare_release(root, "packages/tool", "0.3.0") == []
    text = (root / "packages" / "tool" / "CHANGELOG.md").read_text()
    assert text.index("## 0.3.0") < text.index("## 0.2.0")


def _artifact_remote(tmp_path: Path) -> tuple[Path, Path]:
    remote = tmp_path / "artifact.git"
    remote.mkdir()
    _git(remote, "init", "--bare", "--initial-branch=main")
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "copier.yml").write_text("kind:\n  type: str\n")
    (templates / "project").mkdir()
    (templates / "project" / "tasks.py").write_text("plugin\n")
    return remote, templates


def test_the_snapshot_publishes_and_is_idempotent(tmp_path: Path) -> None:
    remote, templates = _artifact_remote(tmp_path)
    first = publish_templates(templates, "0.0.2", str(remote), author="T <t@l>")
    assert first == "published v0.0.2"
    again = publish_templates(templates, "0.0.2", str(remote), author="T <t@l>")
    assert again == "v0.0.2 already published with this content"
    # The acceptance diff: the artifact tree at the tag is templates/.
    check = tmp_path / "check"
    _git(tmp_path, "clone", str(remote), "check")
    _git(check, "checkout", "v0.0.2")
    shutil.rmtree(check / ".git")  # --no-index would walk the pack files
    diff = subprocess.run(
        ["git", "diff", "--no-index", str(templates), str(check)],
        capture_output=True,
        text=True,
    )
    assert diff.returncode == 0 and diff.stdout == ""


def test_the_same_version_with_different_content_refuses(tmp_path: Path) -> None:
    remote, templates = _artifact_remote(tmp_path)
    publish_templates(templates, "0.0.2", str(remote), author="T <t@l>")
    (templates / "copier.yml").write_text("kind:\n  type: str\n  default: x\n")
    with pytest.raises(_FAILURES) as caught:
        publish_templates(templates, "0.0.2", str(remote), author="T <t@l>")
    assert "immutable" in str(caught.value)
    assert (
        publish_templates(templates, "0.0.3", str(remote), author="T <t@l>")
        == "published v0.0.3"
    )


def test_floor_bumps_move_both_homes(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    _git(root, "tag", "packages/core/v0.2.0")
    git = GitOps(root)
    assert latest_released(git.tags())["packages/core"] == "0.2.0"
    changed = bump_floors(root, git)
    assert changed == ["packages/tool: floor on packages/core 0.1.0 -> 0.2.0"]
    contract = (root / "packages" / "tool" / "livery.toml").read_text()
    assert 'floor = "0.2.0"' in contract
    pyproject = (root / "packages" / "tool" / "pyproject.toml").read_text()
    assert "livery-core>=0.2.0" in pyproject
    assert bump_floors(root, git) == []  # already at the newest release
