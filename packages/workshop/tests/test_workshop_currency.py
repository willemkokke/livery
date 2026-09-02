"""sync's bring-current ladder and integrate: parks and gates first."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from footman import Failed

from livery.workshop._git_ops import GitOps
from livery.workshop._submit import prepare
from livery.workshop._sync import bring_current, integrate

_FAILURES = (SystemExit, Failed)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout


def _rig(tmp_path: Path) -> tuple[Path, Path]:
    origin = tmp_path / "origin.git"
    origin.mkdir()
    _git(origin, "init", "--bare", "--initial-branch=main")
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", str(origin), "clone")
    _git(clone, "config", "user.email", "me@livery.local")
    _git(clone, "config", "user.name", "Me")
    (clone / "seed.txt").write_text("seed\n")
    _git(clone, "add", ".")
    _git(clone, "commit", "-m", "chore: seed")
    _git(clone, "push", "-u", "origin", "main")
    return clone, origin


def _other(tmp_path: Path, origin: Path, email: str = "them@livery.local") -> Path:
    other = tmp_path / f"other-{email.split('@')[0]}"
    if other.is_dir():
        _git(other, "pull", "--ff-only", "origin", "main")
        return other
    _git(tmp_path, "clone", str(origin), other.name)
    _git(other, "config", "user.email", email)
    _git(other, "config", "user.name", "Them")
    return other


def _advance_main(tmp_path: Path, origin: Path, name: str = "upstream.txt") -> None:
    other = _other(tmp_path, origin, email="ci@livery.local")
    (other / name).write_text("x\n")
    _git(other, "add", ".")
    _git(other, "commit", "-m", f"feat: {name}")
    _git(other, "push", "origin", "main")


def test_a_conflicted_rebase_parks_and_restores_the_branch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clone, origin = _rig(tmp_path)
    _git(clone, "checkout", "-b", "feat/1-work")
    (clone / "seed.txt").write_text("mine\n")
    _git(clone, "commit", "-am", "feat: my side")
    other = _other(tmp_path, origin, email="ci@livery.local")
    (other / "seed.txt").write_text("theirs\n")
    _git(other, "commit", "-am", "feat: their side")
    _git(other, "push", "origin", "main")
    before = _git(clone, "rev-parse", "HEAD").strip()
    bring_current(clone, GitOps(clone), interactive=False)
    out = capsys.readouterr().out
    assert "conflicts" in out and "fm integrate" in out
    # The attempt was aborted: the branch is exactly as it was.
    assert _git(clone, "rev-parse", "HEAD").strip() == before
    assert "rebase" not in _git(clone, "status")


def test_foreign_commits_gate_the_rebase_and_teach_integrate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clone, origin = _rig(tmp_path)
    _git(clone, "checkout", "-b", "feat/1-shared")
    (clone / "mine.txt").write_text("m\n")
    _git(clone, "add", ".")
    _git(clone, "commit", "-m", "feat: mine")
    _git(clone, "push", "-u", "origin", "feat/1-shared")
    # A colleague adds to the shared branch; we pull their commit.
    other = _other(tmp_path, origin)
    _git(other, "checkout", "feat/1-shared")
    (other / "theirs.txt").write_text("t\n")
    _git(other, "add", ".")
    _git(other, "commit", "-m", "feat: theirs")
    _git(other, "push", "origin", "feat/1-shared")
    _git(clone, "pull", "--ff-only", "origin", "feat/1-shared")
    _advance_main(tmp_path, origin)
    before = _git(clone, "rev-parse", "HEAD").strip()
    bring_current(clone, GitOps(clone), interactive=False)
    out = capsys.readouterr().out
    assert "them@livery.local" in out and "fm integrate" in out
    assert _git(clone, "rev-parse", "HEAD").strip() == before


def test_a_clean_rebase_lands_and_the_remote_follows_leased(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clone, origin = _rig(tmp_path)
    _git(clone, "checkout", "-b", "feat/1-work")
    (clone / "mine.txt").write_text("m\n")
    _git(clone, "add", ".")
    _git(clone, "commit", "-m", "feat: mine")
    _git(clone, "push", "-u", "origin", "feat/1-work")
    _advance_main(tmp_path, origin)
    bring_current(clone, GitOps(clone), interactive=False)
    out = capsys.readouterr().out
    assert "rebased feat/1-work onto origin/main" in out
    assert "leased force-push" in out
    remote = _git(clone, "ls-remote", "origin", "feat/1-work").split()[0]
    assert remote == _git(clone, "rev-parse", "HEAD").strip()
    # Linear: the rebase left no merge commit behind.
    log = _git(clone, "log", "--merges", "--format=%s", "feat/1-work")
    assert log.strip() == ""


def test_a_moved_remote_branch_fast_forwards_when_behind_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clone, origin = _rig(tmp_path)
    _git(clone, "checkout", "-b", "feat/1-shared")
    _git(clone, "push", "-u", "origin", "feat/1-shared")
    other = _other(tmp_path, origin)
    _git(other, "checkout", "feat/1-shared")
    (other / "theirs.txt").write_text("t\n")
    _git(other, "add", ".")
    _git(other, "commit", "-m", "feat: theirs")
    _git(other, "push", "origin", "feat/1-shared")
    bring_current(clone, GitOps(clone), interactive=False)
    assert "fast-forwarded" in capsys.readouterr().out
    assert (clone / "theirs.txt").is_file()


def test_main_only_ever_fast_forwards(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clone, origin = _rig(tmp_path)
    _advance_main(tmp_path, origin)
    bring_current(clone, GitOps(clone), interactive=False)
    assert (clone / "upstream.txt").is_file()
    # Diverged main is never rebased and never merged: a note names
    # the move-to-a-branch remedy.
    (clone / "local.txt").write_text("l\n")
    _git(clone, "add", ".")
    _git(clone, "commit", "-m", "feat: stranded on main")
    _advance_main(tmp_path, origin, name="second.txt")
    bring_current(clone, GitOps(clone), interactive=False)
    out = capsys.readouterr().out
    assert "never rebased" in out and "branch" in out


def test_the_untouchables_skip_with_their_notes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clone, origin = _rig(tmp_path)
    _advance_main(tmp_path, origin)
    # A workflow branch belongs to the engine.
    _git(clone, "checkout", "-b", "workflow/update/templates")
    before = _git(clone, "rev-parse", "HEAD").strip()
    bring_current(clone, GitOps(clone), interactive=False)
    assert _git(clone, "rev-parse", "HEAD").strip() == before
    # A dirty tree is never moved.
    _git(clone, "checkout", "-b", "feat/1-dirty")
    (clone / "wip.txt").write_text("w\n")
    bring_current(clone, GitOps(clone), interactive=False)
    assert "uncommitted changes" in capsys.readouterr().out
    (clone / "wip.txt").unlink()
    # Detached HEAD names no branch.
    _git(clone, "checkout", "--detach")
    bring_current(clone, GitOps(clone), interactive=False)
    assert "detached" in capsys.readouterr().out


def test_integrate_merges_the_base_in_and_teaches_on_conflict(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clone, origin = _rig(tmp_path)
    monkeypatch.setattr("livery.workshop._sync.workspace_root", lambda: clone)
    monkeypatch.chdir(clone)
    _git(clone, "checkout", "-b", "feat/1-work")
    (clone / "mine.txt").write_text("m\n")
    _git(clone, "add", ".")
    _git(clone, "commit", "-m", "feat: mine")
    _advance_main(tmp_path, origin)
    integrate()
    out = capsys.readouterr().out
    assert "merged origin/main into feat/1-work" in out
    assert (clone / "upstream.txt").is_file()
    integrate()
    assert "already current" in capsys.readouterr().out
    # A conflicting advance stops with git's words and the recovery.
    other = _other(tmp_path, origin, email="ci@livery.local")
    (other / "mine.txt").write_text("theirs\n")
    _git(other, "add", ".")
    _git(other, "commit", "-m", "feat: their conflicting side")
    _git(other, "push", "origin", "main")
    with pytest.raises(_FAILURES) as caught:
        integrate()
    assert "Resolve them" in str(caught.value)


def test_merge_subjects_never_default_the_title_or_count(
    tmp_path: Path,
) -> None:
    clone, origin = _rig(tmp_path)
    _git(clone, "checkout", "-b", "feat/1-work")
    (clone / "mine.txt").write_text("m\n")
    _git(clone, "add", ".")
    _git(clone, "commit", "-m", "feat: the real intent")
    _advance_main(tmp_path, origin)
    _git(clone, "fetch", "origin")
    _git(clone, "merge", "--no-edit", "origin/main")
    plan = prepare(GitOps(clone))
    assert plan.title == "feat: the real intent"
