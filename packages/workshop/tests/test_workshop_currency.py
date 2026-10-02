"""sync's bring-current ladder, integrate, and the sweep of a package's leftovers."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from livery.footman.api import Failed
from livery.workshop._git_ops import GitOps
from livery.workshop._packages import discover_packages
from livery.workshop._submit import prepare
from livery.workshop._sync import bring_current, integrate, sweep_residue
from workshop_seeds import Seeds, _seed_home, pushed, seed_copier  # noqa: F401

_FAILURES = (SystemExit, Failed)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout


def _rig(seeds: Seeds) -> tuple[Path, Path]:
    """The shared clone and the bare origin behind it, copied for this test."""
    root = seeds("pushed", pushed)
    return root / "work", root / "origin.git"


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
    seeds: Seeds, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clone, origin = _rig(seeds)
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


def test_a_rebase_a_refused_signature_stopped_prints_gits_words_not_conflicts(
    seeds: Seeds,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clone, origin = _rig(seeds)
    _git(clone, "checkout", "-b", "feat/1-work")
    (clone / "mine.txt").write_text("m\n")
    _git(clone, "add", ".")
    _git(clone, "commit", "-m", "feat: mine")
    _advance_main(tmp_path, origin)
    head = _git(clone, "rev-parse", "HEAD")
    # The commit the rebase writes goes to a signer that cannot run:
    # the merge is clean, and the rebase still stops.
    monkeypatch.setenv("GIT_CONFIG_COUNT", "4")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "true")
    monkeypatch.setenv("GIT_CONFIG_KEY_2", "gpg.format")
    monkeypatch.setenv("GIT_CONFIG_VALUE_2", "openpgp")
    monkeypatch.setenv("GIT_CONFIG_KEY_3", "gpg.program")
    monkeypatch.setenv("GIT_CONFIG_VALUE_3", str(tmp_path / "no-signer"))
    bring_current(clone, GitOps(clone), interactive=False)
    out = capsys.readouterr().out
    assert (
        "left feat/1-work behind origin/main: the rebase stopped, and not on a"
        " conflict. git said:" in out
    )
    assert "gpg failed to sign the data" in out
    assert "has conflicts" not in out
    # Aborted: the branch is exactly as it was.
    assert _git(clone, "rev-parse", "HEAD") == head
    assert _git(clone, "status", "--porcelain") == ""


def test_foreign_commits_gate_the_rebase_and_teach_integrate(
    seeds: Seeds, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clone, origin = _rig(seeds)
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
    seeds: Seeds, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clone, origin = _rig(seeds)
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


# A stacked branch: `fm start --from` records its parent and the commit
# it started from, and sync moves the branch's own commits alone.


def _stacked(clone: Path, *, record_start: bool = True) -> str:
    """A parent of two commits and a child started on it, both pushed.

    The parent's second commit edits a line its first wrote, and the
    child adds a line after it: a squash of the parent then carries
    neither of the parent's commits as they were. Returns the commit the
    child started from.
    """
    _git(clone, "checkout", "-b", "feat/1-parent")
    (clone / "shared.txt").write_text("a\nb\nc\n")
    _git(clone, "add", ".")
    _git(clone, "commit", "-m", "feat: the parent")
    (clone / "shared.txt").write_text("a\nB\nc\n")
    _git(clone, "commit", "-am", "fix: the parent's fix")
    _git(clone, "push", "-u", "origin", "feat/1-parent")
    start = _git(clone, "rev-parse", "HEAD").strip()
    _git(clone, "checkout", "-b", "feat/2-child")
    git = GitOps(clone)
    if record_start:
        git.record_stack("feat/2-child", "feat/1-parent", start)
    else:
        git.config_set("branch.feat/2-child.workshop-parent", "feat/1-parent")
    (clone / "shared.txt").write_text("a\nB\nc\nd\n")
    _git(clone, "commit", "-am", "feat: the child")
    _git(clone, "push", "-u", "origin", "feat/2-child")
    return start


def _squash_parent(tmp_path: Path, origin: Path) -> None:
    """Land the parent on main as one squash commit and delete its branch."""
    other = _other(tmp_path, origin, email="forge@livery.local")
    _git(other, "fetch", "origin")
    _git(other, "merge", "--squash", "origin/feat/1-parent")
    _git(other, "commit", "-m", "feat: the parent (#1)")
    _git(other, "push", "origin", "main")
    _git(other, "push", "origin", "--delete", "feat/1-parent")


def _own(clone: Path, base: str) -> list[str]:
    """The subjects the branch carries beyond *base*, newest first."""
    return _git(clone, "log", "--format=%s", f"{base}..HEAD").split("\n")[:-1]


def test_a_stacked_branch_whose_own_commit_conflicts_is_left_as_it_was(
    seeds: Seeds, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clone, origin = _rig(seeds)
    _stacked(clone)
    _squash_parent(tmp_path, origin)
    # Main then writes the line the child's own commit adds.
    other = _other(tmp_path, origin, email="forge@livery.local")
    (other / "shared.txt").write_text("a\nB\nc\nD\n")
    _git(other, "commit", "-am", "feat: main's own line")
    _git(other, "push", "origin", "main")
    before = _git(clone, "rev-parse", "HEAD").strip()
    bring_current(clone, GitOps(clone), interactive=False)
    assert "the rebase has conflicts" in capsys.readouterr().out
    assert _git(clone, "rev-parse", "HEAD").strip() == before
    assert "rebase" not in _git(clone, "status")
    assert GitOps(clone).stack("feat/2-child")[0] == "feat/1-parent"


def test_a_stacked_branch_with_no_recorded_start_falls_back_to_a_plain_rebase(
    seeds: Seeds, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Started before the start commit was recorded: git's own matching
    # drops a parent that landed as one commit and no other, so a parent
    # of two commits conflicts against its own squash.
    clone, origin = _rig(seeds)
    _stacked(clone, record_start=False)
    _squash_parent(tmp_path, origin)
    before = _git(clone, "rev-parse", "HEAD").strip()
    bring_current(clone, GitOps(clone), interactive=False)
    assert "the rebase has conflicts" in capsys.readouterr().out
    assert _git(clone, "rev-parse", "HEAD").strip() == before


def test_a_stacked_branch_moves_its_own_commits_onto_main_once_its_parent_merged(
    seeds: Seeds, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clone, origin = _rig(seeds)
    _stacked(clone)
    _squash_parent(tmp_path, origin)
    bring_current(clone, GitOps(clone), interactive=False)
    out = capsys.readouterr().out
    assert "rebased feat/2-child onto origin/main" in out
    assert "feat/1-parent has merged: feat/2-child stands on main now" in out
    assert "leased force-push" in out
    assert _own(clone, "origin/main") == ["feat: the child"]
    assert (clone / "shared.txt").read_text() == "a\nB\nc\nd\n"
    assert GitOps(clone).stack("feat/2-child") == ("", "")
    remote = _git(clone, "ls-remote", "origin", "feat/2-child").split()[0]
    assert remote == _git(clone, "rev-parse", "HEAD").strip()


def test_a_stacked_branch_follows_its_parent_when_the_parent_moves(
    seeds: Seeds, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clone, origin = _rig(seeds)
    _stacked(clone)
    # The parent is rewritten on origin: rebased onto a moved main and
    # pushed with force, as a sync of the parent does.
    _advance_main(tmp_path, origin)
    other = _other(tmp_path, origin)
    _git(other, "fetch", "origin")
    _git(other, "checkout", "-b", "feat/1-parent", "origin/feat/1-parent")
    _git(other, "rebase", "origin/main")
    _git(other, "push", "--force", "origin", "feat/1-parent")
    parent_tip = _git(other, "rev-parse", "HEAD").strip()
    bring_current(clone, GitOps(clone), interactive=False)
    assert "rebased feat/2-child onto origin/feat/1-parent" in capsys.readouterr().out
    assert _own(clone, "origin/feat/1-parent") == ["feat: the child"]
    assert GitOps(clone).stack("feat/2-child") == ("feat/1-parent", parent_tip)


def test_a_stacked_branch_stays_on_its_open_parent_when_main_moves(
    seeds: Seeds, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clone, origin = _rig(seeds)
    start = _stacked(clone)
    _advance_main(tmp_path, origin)
    before = _git(clone, "rev-parse", "HEAD").strip()
    bring_current(clone, GitOps(clone), interactive=False)
    assert (
        "feat/2-child is stacked on feat/1-parent: main reaches it when"
        " feat/1-parent merges"
    ) in capsys.readouterr().out
    assert _git(clone, "rev-parse", "HEAD").strip() == before
    assert GitOps(clone).stack("feat/2-child") == ("feat/1-parent", start)


def test_a_moved_remote_branch_fast_forwards_when_behind_only(
    seeds: Seeds, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clone, origin = _rig(seeds)
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
    seeds: Seeds, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clone, origin = _rig(seeds)
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
    seeds: Seeds, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clone, origin = _rig(seeds)
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


def test_a_clone_with_no_origin_has_nothing_to_bring_current(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A local birth has no remote: sync's first act skips and the rest runs.
    _git(tmp_path, "init", "--quiet", "--initial-branch=main")
    _git(
        tmp_path,
        *("-c", "user.email=a@b.c", "-c", "user.name=A", "-c", "commit.gpgsign=false"),
        *("commit", "--quiet", "--allow-empty", "-m", "chore: root"),
    )
    assert not GitOps(tmp_path).has_remote("origin")
    bring_current(tmp_path, GitOps(tmp_path), interactive=False)
    assert "no origin remote: nothing to bring current" in capsys.readouterr().out


def test_integrate_merges_the_base_in_and_teaches_on_conflict(
    seeds: Seeds,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clone, origin = _rig(seeds)
    monkeypatch.setattr(
        "livery.workshop._sync.workspace_root", lambda start=None: clone
    )
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
    seeds: Seeds,
    tmp_path: Path,
) -> None:
    clone, origin = _rig(seeds)
    _git(clone, "checkout", "-b", "feat/1-work")
    (clone / "mine.txt").write_text("m\n")
    _git(clone, "add", ".")
    _git(clone, "commit", "-m", "feat: the real intent")
    _advance_main(tmp_path, origin)
    _git(clone, "fetch", "origin")
    _git(clone, "merge", "--no-edit", "origin/main")
    plan = prepare(GitOps(clone))
    assert plan.title == "feat: the real intent"


def test_a_dirty_behind_main_names_the_refusal_not_local_commits(
    seeds: Seeds, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Issue #123: behind-plus-dirty was read as "local commits". The
    # two states need different acts, so each gets its own words and
    # the fast-forward refusal carries git's reason verbatim.
    clone, origin = _rig(seeds)
    other = _other(tmp_path, origin, email="ci@livery.local")
    (other / "seed.txt").write_text("moved\n")
    _git(other, "commit", "-am", "feat: main moves")
    _git(other, "push", "origin", "main")
    (clone / "seed.txt").write_text("dirty local edit\n")
    bring_current(clone, GitOps(clone), interactive=False)
    out = capsys.readouterr().out
    assert "local commits" not in out
    assert "fast-forward was refused" in out


def test_local_commits_on_main_still_teach_the_branch_move(
    seeds: Seeds, capsys: pytest.CaptureFixture[str]
) -> None:
    clone, _origin = _rig(seeds)
    (clone / "extra.txt").write_text("x\n")
    _git(clone, "add", ".")
    _git(clone, "commit", "-m", "feat: committed straight to main")
    bring_current(clone, GitOps(clone), interactive=False)
    out = capsys.readouterr().out
    assert "local commits" in out and "Move them to a branch" in out


def test_parked_content_survives_integrate_and_the_squash(
    seeds: Seeds,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The 0904 incident's shape, forced: an edit parked as a wip
    # commit, main moving the same file meanwhile, integrate merging
    # main in, and the squash-merge landing. The parked content must
    # be in the squash; the incident lost a plan-note edit somewhere
    # on this path and its drop point was never proven.
    clone, origin = _rig(seeds)
    note = clone / "notes.md"
    note.write_text("top line\n\nmiddle\n\nbottom line\n")
    _git(clone, "add", ".")
    _git(clone, "commit", "-m", "chore: the note")
    _git(clone, "push", "origin", "main")
    # The dirty edit, parked exactly as issue.start --wip parks it.
    note.write_text("top line\n\nmiddle\n\nbottom line\n\nthe parked ruling\n")
    _git(clone, "checkout", "-b", "feat/1-work")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-m", "chore(wip): parked working tree")
    # Main moves the same file at the other end.
    other = _other(tmp_path, origin, email="ci@livery.local")
    _git(other, "pull", "--ff-only", "origin", "main")
    (other / "notes.md").write_text("TOP EDITED\n\nmiddle\n\nbottom line\n")
    _git(other, "add", ".")
    _git(other, "commit", "-m", "feat: main edits the top")
    _git(other, "push", "origin", "main")
    # Integrate, then the squash-merge a landing performs.
    monkeypatch.setattr(
        "livery.workshop._sync.workspace_root", lambda start=None: clone
    )
    monkeypatch.chdir(clone)
    integrate()
    assert "merged origin/main" in capsys.readouterr().out
    merged = note.read_text()
    assert "the parked ruling" in merged and "TOP EDITED" in merged
    lander = _other(tmp_path, origin, email="squash@livery.local")
    _git(lander, "pull", "--ff-only", "origin", "main")
    _git(clone, "push", "origin", "feat/1-work")
    _git(lander, "fetch", "origin", "feat/1-work")
    _git(lander, "merge", "--squash", "origin/feat/1-work")
    _git(lander, "commit", "-m", "chore: the squash")
    landed = (lander / "notes.md").read_text()
    assert "the parked ruling" in landed
    assert "TOP EDITED" in landed
    # And the squash itself carries the parked change as a diff.
    shown = _git(lander, "show", "HEAD", "--", "notes.md")
    assert "+the parked ruling" in shown


def test_a_merged_reserved_branch_is_stepped_off_by_sync(
    seeds: Seeds,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from livery.forge.testing import FakeForge
    from livery.workshop import _sync

    clone, _origin = _rig(seeds)
    branch = "workflow/release/x"
    _git(clone, "checkout", "-b", branch)
    (clone / "stamp.txt").write_text("s\n")
    _git(clone, "add", ".")
    _git(clone, "commit", "-m", "chore(release): livery-x v0.1.0")
    _git(clone, "push", "-u", "origin", branch)
    tip = _git(clone, "rev-parse", "HEAD").strip()
    fake = FakeForge()
    fake.create_repo("o", "r")
    repo = fake.repository("o", "r")

    # The forge cannot be asked: the engine owns the branch, nothing moves.
    def _down(_root: Path) -> object:
        raise RuntimeError("the forge is down")

    monkeypatch.setattr(_sync, "_repository", _down)
    bring_current(clone, GitOps(clone), interactive=False)
    assert "the engine owns it" in capsys.readouterr().out
    assert _git(clone, "rev-parse", "--abbrev-ref", "HEAD").strip() == branch
    # The pull request is open: the same.
    fake.push("o", "r", branch, sha=tip)
    repo.pr.open(branch, "main", "chore(release): released livery-x v0.1.0")
    monkeypatch.setattr(_sync, "_repository", lambda _root: repo)
    bring_current(clone, GitOps(clone), interactive=False)
    assert "the engine owns it" in capsys.readouterr().out
    # Merged, with a commit past the merge: kept and named.
    fake.settle("o", "r", tip)
    repo.pr.merge_now(1, title="chore(release): released livery-x v0.1.0")
    (clone / "after.txt").write_text("a\n")
    _git(clone, "add", ".")
    _git(clone, "commit", "-m", "chore: after the merge")
    bring_current(clone, GitOps(clone), interactive=False)
    out = capsys.readouterr().out
    assert (
        "PR #1 merged, but the branch holds 1 commit(s) past the merged head; kept"
        in out
    )
    assert _git(clone, "rev-parse", "--abbrev-ref", "HEAD").strip() == branch
    # At the merged head: sync steps off and the branch goes.
    _git(clone, "reset", "--hard", tip)
    bring_current(clone, GitOps(clone), interactive=False)
    out = capsys.readouterr().out
    assert (
        "PR #1 merged; stepping off" in out and f"deleted {branch}; back on main" in out
    )
    assert _git(clone, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    assert _git(clone, "branch", "--list", branch).strip() == ""


def test_integrate_matches_the_lock_when_the_move_touched_it(
    seeds: Seeds,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from livery.workshop import _reconcile, _sync, _uv

    clone, _origin = _rig(seeds)
    before = _git(clone, "rev-parse", "HEAD").strip()
    synced: list[Path] = []
    recorded: list[Path] = []
    monkeypatch.setattr(_uv, "run_uv", lambda *args, root: synced.append(root))
    monkeypatch.setattr(
        "livery.workshop._tool_tasks.sync_tools", lambda root, **kwargs: None
    )
    monkeypatch.setattr(
        _reconcile, "record_receipt", lambda root: recorded.append(root)
    )
    # A move that touched nothing the venv reads: nothing happens.
    (clone / "notes.md").write_text("n\n")
    _git(clone, "add", ".")
    _git(clone, "commit", "-m", "docs: notes")
    _sync.match_lock(clone, GitOps(clone), since=before)
    assert synced == [] and "matching the lock" not in capsys.readouterr().out
    # A member's manifest changed across the move: the sync runs at
    # the verb's end and the receipts record it.
    member = clone / "packages" / "x"
    member.mkdir(parents=True)
    (member / "pyproject.toml").write_text('[project]\nname = "x"\n')
    _git(clone, "add", ".")
    _git(clone, "commit", "-m", "feat: x")
    _sync.match_lock(clone, GitOps(clone), since=before)
    out = capsys.readouterr().out
    assert "matching the lock: the merge changed packages/x/pyproject.toml" in out
    assert "matched the lock" in out
    assert synced == [clone] and recorded == [clone]


# A removed package's leftovers. What stays first: work git does not
# ignore, a machine secret, a tracked directory, git failing.

_IGNORED = "__pycache__/\ndist/\n*.env.local\n"


def _repository(tmp_path: Path) -> Path:
    """A repository with one commit, ignoring bytecode, built wheels and secrets."""
    root = tmp_path / "repository"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "me@livery.local")
    _git(root, "config", "user.name", "Me")
    (root / ".gitignore").write_text(_IGNORED)
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "chore: ignore")
    return root


def _leave_residue(root: Path, name: str) -> Path:
    """Leave bytecode and a built wheel in ``packages/<name>``, both ignored."""
    directory = root / "packages" / name
    cache = directory / "src" / name / "__pycache__"
    cache.mkdir(parents=True)
    (cache / "_codec.cpython-314.pyc").write_bytes(b"\0")
    (directory / "dist").mkdir()
    (directory / "dist" / f"{name}-0.1.0-py3-none-any.whl").write_bytes(b"PK")
    return directory


def _no_contract_named(root: Path) -> str:
    with pytest.raises(ValueError, match="packages missing their contracts") as caught:
        discover_packages(root)
    return str(caught.value).splitlines()[-1].strip()


def test_the_sweep_keeps_a_directory_holding_work_git_does_not_ignore(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path)
    directory = _leave_residue(root, "fresh")
    (directory / "notes.md").write_text("a package being written\n")
    assert sweep_residue(root) == [
        "  packages/fresh: no workshop.toml, and it holds files git neither"
        " tracks nor ignores (packages/fresh/notes.md); kept"
    ]
    assert (directory / "notes.md").is_file()
    assert _no_contract_named(root) == "fresh: no workshop.toml"


def test_the_sweep_keeps_leftovers_holding_a_machine_secret(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    directory = _leave_residue(root, "gone")
    (directory / ".repo.env.local").write_text("TOKEN=x\n")
    assert sweep_residue(root) == [
        "  packages/gone: no workshop.toml, and it holds a machine secret no"
        " checkout restores (packages/gone/.repo.env.local); kept: move the"
        " secret out, then delete the directory"
    ]
    assert (directory / ".repo.env.local").is_file()
    assert _no_contract_named(root) == "gone: no workshop.toml"


def test_the_sweep_leaves_a_tracked_directory_without_a_contract_to_discovery(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path)
    directory = _leave_residue(root, "half")
    (directory / "README.md").write_text("half a package\n")
    _git(root, "add", "packages/half/README.md")
    assert sweep_residue(root) == []
    assert directory.is_dir()
    assert _no_contract_named(root) == "half: no workshop.toml"


def test_the_sweep_names_git_failing_and_keeps_the_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repository(tmp_path)
    directory = _leave_residue(root, "gone")
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "nowhere"))
    [line] = sweep_residue(root)
    assert line.startswith(
        "  packages/gone: no workshop.toml, and git could not say what it holds ("
    )
    assert "not a git repository" in line and line.endswith("); kept")
    assert directory.is_dir()
    assert _no_contract_named(root) == "gone: no workshop.toml"


def test_the_sweep_says_nothing_when_every_directory_has_its_contract(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path)
    assert sweep_residue(root) == []
    member = root / "packages" / "kept"
    member.mkdir(parents=True)
    (member / "workshop.toml").write_text('kind = "python"\nname = "kept"\n')
    (member / "pyproject.toml").write_text('[project]\nname = "kept"\n')
    assert sweep_residue(root) == []
    assert [package.name for package in discover_packages(root)] == ["kept"]


def _removed_upstream(tmp_path: Path, clone: Path, origin: Path) -> Path:
    """Push ``packages/gone``, leave its ignored files, then remove it on origin."""
    (clone / ".gitignore").write_text(_IGNORED)
    package = clone / "packages" / "gone"
    package.mkdir(parents=True)
    (package / "workshop.toml").write_text('kind = "python"\nname = "gone"\n')
    (package / "pyproject.toml").write_text('[project]\nname = "gone"\n')
    _git(clone, "add", ".")
    _git(clone, "commit", "-q", "-m", "feat: gone")
    _git(clone, "push", "-q", "origin", "HEAD")
    _leave_residue(clone, "gone")
    other = _other(tmp_path, origin)
    _git(other, "pull", "-q", "--ff-only", "origin", "main")
    _git(other, "rm", "-r", "-q", "packages/gone")
    _git(other, "commit", "-q", "-m", "feat!: gone goes")
    _git(other, "push", "-q", "origin", "main")
    return package


def test_a_removed_packages_leftovers_go_once_the_checkout_moves_past_the_removal(
    seeds: Seeds, tmp_path: Path
) -> None:
    import livery.footman.api as footman

    clone, origin = _rig(seeds)
    package = _removed_upstream(tmp_path, clone, origin)
    bring_current(clone, GitOps(clone), interactive=False)
    # Git took the tracked files and kept the ignored ones: discovery
    # refuses the directory and names the sync that removes it.
    assert not (package / "workshop.toml").exists() and package.is_dir()
    assert _no_contract_named(clone) == (
        "gone: no workshop.toml, and git tracks nothing under packages/gone:"
        " it holds only ignored files, what a removed package leaves behind."
        f" `{footman.prog()} sync` removes the directory"
    )
    assert sweep_residue(clone) == [
        "  packages/gone: removed 2 ignored file(s) a removed package left behind"
    ]
    assert not package.exists()
    assert discover_packages(clone) == ()
    assert sweep_residue(clone) == []


def test_integrate_sweeps_what_the_merge_left_behind(
    seeds: Seeds,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from livery.workshop import _reconcile, _uv

    clone, origin = _rig(seeds)
    monkeypatch.setattr(
        "livery.workshop._sync.workspace_root", lambda start=None: clone
    )
    # The merge deletes a member's manifest, so the lock match runs.
    monkeypatch.setattr(_uv, "run_uv", lambda *args, root: None)
    monkeypatch.setattr(_reconcile, "record_receipt", lambda at: None)
    monkeypatch.chdir(clone)
    package = _removed_upstream(tmp_path, clone, origin)
    _git(clone, "checkout", "-q", "-b", "feat/1-work")
    (clone / "mine.txt").write_text("m\n")
    _git(clone, "add", "mine.txt")
    _git(clone, "commit", "-q", "-m", "feat: mine")
    integrate()
    out = capsys.readouterr().out
    assert "merged origin/main into feat/1-work" in out
    assert (
        "packages/gone: removed 2 ignored file(s) a removed package left behind" in out
    )
    assert not package.exists()


def test_the_sync_sweeps_before_anything_discovers_packages(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from livery.workshop import _reconcile, _sync, _uv

    root = _repository(tmp_path)
    (root / "workshop.toml").write_text("[workspace]\n")
    package = _leave_residue(root, "gone")
    seen: list[tuple[str, ...]] = []

    def deliver(at: Path) -> list[str]:
        seen.append(tuple(package.name for package in discover_packages(at)))
        return []

    monkeypatch.setattr(_sync, "workspace_root", lambda start=None: root)
    monkeypatch.setattr(_sync, "bring_current", lambda *args, **kwargs: None)
    monkeypatch.setattr(_sync, "fetch_store_lines", lambda at: [])
    monkeypatch.setattr(_sync, "sync_workspace", deliver)
    monkeypatch.setattr(
        "livery.workshop._tool_tasks.sync_tools", lambda at, **kwargs: None
    )
    monkeypatch.setattr(_uv, "run_uv", lambda *args, root: None)
    monkeypatch.setattr(_reconcile, "record_receipt", lambda at: None)
    _sync.sync()
    assert seen == [()]
    assert not package.exists()
    assert "packages/gone: removed 2 ignored file(s)" in capsys.readouterr().out
