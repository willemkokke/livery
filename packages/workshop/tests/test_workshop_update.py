"""The update wave's flow, the arming ladder, and the package generator."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from footman import Failed

from livery.workshop._git_ops import GitOps
from livery.workshop._submit import arming_reason, ci_automerge
from livery.workshop._templates import new_package
from livery.workshop._update import _align_answers_source, update_flow

_FAILURES = (SystemExit, Failed)

ROOT = Path(__file__).resolve().parents[3]


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=True)


def _instance(tmp_path: Path) -> Path:
    """A clean clone of a bare origin, current with its own render."""
    origin = tmp_path / "origin.git"
    origin.mkdir()
    _git(origin, "init", "--bare", "--initial-branch=main")
    root = tmp_path / "ws"
    _git(tmp_path, "clone", str(origin), "ws")
    _git(root, "config", "user.email", "t@livery.local")
    _git(root, "config", "user.name", "T")
    import shutil

    shutil.copytree(ROOT / "templates", root / "templates")
    shutil.copy(ROOT / ".copier-answers.yml", root / ".copier-answers.yml")
    # The renderer reads the contract for its source, so the seed line
    # comes first; the render then overwrites the file completely.
    (root / "livery.toml").write_text('[workspace]\ntemplates = "templates"\n')
    from livery.workshop._sync import sync_workspace
    from livery.workshop._templates import apply_project

    apply_project(root)
    (root / "packages").mkdir(exist_ok=True)
    # The first sync seeds CLAUDE.project.md; committing it makes the
    # instance current, the state the no-op assertions need.
    sync_workspace(root)
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "chore: seed")
    _git(root, "push", "-u", "origin", "main")
    return root


def test_a_current_instance_updates_to_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _instance(tmp_path)
    update_flow(root, GitOps(root), armed=False)
    out = capsys.readouterr().out
    assert "nothing to update" in out


def test_the_wave_refuses_a_dirty_tree_and_a_feature_branch(
    tmp_path: Path,
) -> None:
    root = _instance(tmp_path)
    git = GitOps(root)
    git.create_branch("feat/elsewhere")
    with pytest.raises(_FAILURES):
        update_flow(root, git, armed=False)
    _git(root, "checkout", "main")
    (root / "stray.txt").write_text("dirty\n")
    with pytest.raises(_FAILURES):
        update_flow(root, git, armed=False)


def test_a_changed_instance_branches_and_submits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _instance(tmp_path)
    # Drift one rendered file; the wave re-renders it and submits.
    (root / "livery.toml").write_text('[workspace]\ntemplates = "templates"\n')
    _git(root, "commit", "-am", "chore: drift")
    _git(root, "push", "origin", "main")
    calls: dict[str, object] = {}

    def fake_submit(repo: object, git: GitOps, **kwargs: object) -> int:
        calls.update(kwargs)
        return 7

    monkeypatch.setattr("livery.workshop._submit.submit_flow", fake_submit)
    monkeypatch.setattr(
        "livery.workshop._forge_lane.this_repository", lambda _root: object()
    )
    update_flow(root, GitOps(root), armed=True)
    assert calls["title"] == "chore: the update wave"
    assert calls["armed"] is True
    assert GitOps(root).current_branch().startswith("chore/update-")


def test_align_answers_source_follows_the_contract(tmp_path: Path) -> None:
    answers = tmp_path / ".copier-answers.yml"
    answers.write_text("_src_path: templates\nkind: project\n")
    notes = _align_answers_source(tmp_path, "https://example.com/fork")
    assert notes and "follows the contract" in notes[0]
    assert "_src_path: https://example.com/fork" in answers.read_text()
    assert _align_answers_source(tmp_path, "https://example.com/fork") == []
    assert _align_answers_source(tmp_path / "absent", "x") == []


def test_the_arming_ladder_names_its_level(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("LIVERY_AUTOMERGE", raising=False)
    assert "this invocation" in arming_reason(armed=True, flag_given=True)
    assert "--no-armed" in arming_reason(armed=False, flag_given=True)
    assert "nothing configured" in arming_reason(armed=False, flag_given=False)
    (tmp_path / "livery.toml").write_text("[ci]\nautomerge = true\n")
    assert "committed repo policy" in arming_reason(armed=True, flag_given=False)
    assert ci_automerge() is True
    monkeypatch.setenv("LIVERY_AUTOMERGE", "1")
    assert "standing preference" in arming_reason(armed=True, flag_given=False)


def test_new_package_renders_and_wires(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _instance(tmp_path)
    monkeypatch.chdir(root)

    real_run = subprocess.run

    def fake_run(cmd: list[str], **kwargs: object):
        if cmd[:2] == ["uv", "lock"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return real_run(cmd, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("livery.workshop._templates.subprocess.run", fake_run)
    with pytest.raises(_FAILURES):
        new_package("Bad Name")
    new_package("scratch")
    assert (root / "packages" / "scratch" / "livery.toml").is_file()
    assert "livery-scratch" in (root / "pyproject.toml").read_text()
    assert "dir: scratch" in (root / ".copier-answers.yml").read_text()
    with pytest.raises(_FAILURES):
        new_package("scratch")  # already exists
