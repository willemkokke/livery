"""The content channel: materialised, idempotent, override-respecting."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from livery.workshop._materialise import materialise
from livery.workshop._sync import sync_workspace

ROOT = Path(__file__).resolve().parents[3]


def _workspace(tmp_path: Path) -> Path:
    (tmp_path / "livery.toml").write_text('[workspace]\nlayers = ["livery.workshop"]\n')
    return tmp_path


def test_sync_is_idempotent(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    first = sync_workspace(root)
    assert first  # the first run has things to say
    assert sync_workspace(root) == []  # the second has nothing


def test_the_stub_imports_guidance_first_then_the_instance(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    sync_workspace(root)
    lines = (root / "CLAUDE.md").read_text().splitlines()
    imports = [line for line in lines if line.startswith("@")]
    assert imports[0] == "@.workshop/interaction-voice.md"
    assert imports[1] == "@.workshop/documentation-standards.md"
    assert imports[-1] == "@CLAUDE.project.md"
    for line in imports[:-1]:
        assert (root / line[1:]).is_file()


def test_skills_and_hooks_are_materialised(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    sync_workspace(root)
    assert (root / ".claude" / "skills" / "create-plan" / "SKILL.md").is_file()
    assert (root / ".claude" / "hooks" / "ruff_fix.py").is_file()
    ignore = (root / ".claude" / "skills" / ".gitignore").read_text()
    assert "/create-plan\n" in ignore


def test_a_local_override_is_kept_and_named(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    sync_workspace(root)
    override = root / ".claude" / "skills" / "create-plan"
    os.unlink(override)  # replace the link with a differing real dir
    override.mkdir()
    (override / "SKILL.md").write_text("my own version\n")
    lines = sync_workspace(root)
    assert any("local override kept" in line for line in lines)
    assert (override / "SKILL.md").read_text() == "my own version\n"
    ignore = (root / ".claude" / "skills" / ".gitignore").read_text()
    assert "/create-plan\n" not in ignore  # the override commits normally


def test_an_identical_committed_copy_is_reclaimed(tmp_path: Path) -> None:
    source = tmp_path / "content" / "skills"
    (source / "thing").mkdir(parents=True)
    (source / "thing" / "SKILL.md").write_text("shipped\n")
    repo = tmp_path / "repo"
    committed = repo / ".claude" / "skills" / "thing"
    committed.mkdir(parents=True)
    (committed / "SKILL.md").write_text("shipped\n")
    lines = materialise(repo, source, "skills")
    assert any("reclaimed" in line for line in lines)
    assert (repo / ".claude" / "skills" / "thing" / "SKILL.md").is_file()


def test_no_longer_shipped_entries_are_pruned(tmp_path: Path) -> None:
    source = tmp_path / "content" / "skills"
    (source / "old").mkdir(parents=True)
    (source / "old" / "SKILL.md").write_text("v1\n")
    repo = tmp_path / "repo"
    materialise(repo, source, "skills")
    (source / "old" / "SKILL.md").unlink()
    (source / "old").rmdir()
    (source / "new").mkdir()
    (source / "new" / "SKILL.md").write_text("v2\n")
    lines = materialise(repo, source, "skills")
    assert any("no longer shipped" in line for line in lines)
    assert not (repo / ".claude" / "skills" / "old").exists()
    assert (repo / ".claude" / "skills" / "new" / "SKILL.md").is_file()


def _tracked_state() -> str:
    return subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def test_the_monorepo_is_in_sync() -> None:
    # The dogfood check. A fresh checkout has no .workshop/ and no
    # materialised links (both gitignored), so the first sync may
    # speak; after it, a second run says nothing and no tracked file
    # changed, so the committed state and the shipped content agree.
    before = _tracked_state()
    sync_workspace(ROOT)
    assert sync_workspace(ROOT) == []
    assert _tracked_state() == before
