"""Every function of livery.git against a real repository, failures first."""

from __future__ import annotations

import subprocess
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from os.path import basename
from pathlib import Path
from typing import Any

import pytest

import livery.toolroom.tools as tools
from livery import git
from livery.git import _ops


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout


def _identify(cwd: Path) -> None:
    for key, value in (
        ("user.name", "tester"),
        ("user.email", "tester@example.invalid"),
        ("commit.gpgsign", "false"),
        ("tag.gpgsign", "false"),
    ):
        _git(cwd, "config", key, value)


def _commit(cwd: Path, name: str, text: str, message: str) -> str:
    (cwd / name).write_text(text)
    _git(cwd, "add", "-A")
    _git(cwd, "commit", "-q", "-m", message)
    return _git(cwd, "rev-parse", "HEAD").strip()


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, Path]:
    """A bare origin and a clone on main with one pushed commit."""
    origin = tmp_path / "origin.git"
    origin.mkdir()
    _git(origin, "init", "-q", "--bare", "--initial-branch=main")
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", str(origin), "clone")
    _identify(clone)
    _commit(clone, "seed.txt", "seed\n", "chore: seed")
    _git(clone, "push", "-q", "-u", "origin", "main")
    return clone, origin


def _second_clone(tmp_path: Path, origin: Path) -> Path:
    other = tmp_path / "other"
    _git(tmp_path, "clone", "-q", str(origin), "other")
    _identify(other)
    return other


@contextmanager
def _counting() -> Iterator[Counter[str]]:
    counts: Counter[str] = Counter()
    original = subprocess.Popen.__init__

    def counting(self: subprocess.Popen[Any], args: Any, *rest: Any, **kw: Any) -> None:
        argv = (
            [str(a) for a in args] if isinstance(args, (list, tuple)) else [str(args)]
        )
        counts[basename(argv[0]).removesuffix(".exe") if argv else ""] += 1
        original(self, args, *rest, **kw)

    subprocess.Popen.__init__ = counting  # type: ignore[method-assign]
    try:
        yield counts
    finally:
        subprocess.Popen.__init__ = original  # type: ignore[method-assign]


class _Failing:
    """A git handle whose every call exits *code* with *words*."""

    def __init__(self, code: int, words: str = "boom") -> None:
        self.code = code
        self.words = words

    def opts(self, **_: Any) -> _Failing:
        return self

    def __call__(self, *args: str) -> tools.Result:
        return tools.Result(code=self.code, stdout="", stderr=self.words)


# --- the door ----------------------------------------------------------------


def test_run_raises_with_gits_words_and_call_returns_the_code(
    repo: tuple[Path, Path],
) -> None:
    clone, _ = repo
    with pytest.raises(git.GitError) as caught:
        git.run(clone, "rev-parse", "--verify", "nonesuch")
    assert "git rev-parse --verify nonesuch exited" in str(caught.value)
    assert "nonesuch" in str(caught.value)
    assert git.call(clone, "rev-parse", "--verify", "nonesuch").code != 0
    assert git.run(clone, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"


# --- failures the real git cannot stage, forced through a failing handle ------


def test_a_remote_probe_raises_on_any_failure_but_a_missing_remote(
    repo: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    clone, _ = repo
    assert git.has_remote(clone)
    assert not git.has_remote(clone, "nonesuch")
    monkeypatch.setattr(tools, "git", _Failing(128, "fatal: broken"))
    with pytest.raises(git.GitError, match="fatal: broken"):
        git.has_remote(clone)


def test_a_conflict_probe_raises_on_any_failure_but_a_conflict(
    repo: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    clone, _ = repo
    assert not git.conflicts_with_base(clone, "main")
    monkeypatch.setattr(tools, "git", _Failing(128, "fatal: broken"))
    with pytest.raises(git.GitError, match="merge-tree exited 128"):
        git.conflicts_with_base(clone, "main")


def test_a_local_branch_probe_raises_on_any_failure_but_absence(
    repo: tuple[Path, Path], tmp_path: Path
) -> None:
    clone, _ = repo
    assert git.local_branch_exists(clone, "main")
    assert not git.local_branch_exists(clone, "nonesuch")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    with pytest.raises(git.GitError, match="is not a git repository"):
        git.local_branch_exists(elsewhere, "main")


def test_an_object_the_checkout_lacks_raises_naming_it(
    repo: tuple[Path, Path],
) -> None:
    clone, _ = repo
    with pytest.raises(git.GitError, match=f"{'f' * 40} is not in the repository"):
        git.tree_diff(clone, "f" * 40, git.head_sha(clone))
    with pytest.raises(git.GitError, match="exited 128"):
        git.commit_message(clone, "f" * 40)


def test_a_rev_parse_answer_that_is_no_sha_is_a_failure(
    repo: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    clone, _ = repo
    monkeypatch.setattr(_ops, "run", lambda *_: "")
    with pytest.raises(git.GitError, match="named no object"):
        git.commit_message(clone, "main@{1}")


def test_the_working_tree_id_raises_when_the_scratch_index_cannot_be_written(
    repo: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    clone, _ = repo
    real = tools.git

    class _AddFails:
        def opts(self, **kw: Any) -> Any:
            if "env" in kw:
                return _Failing(1, "index locked")
            return real.opts(**kw)

    monkeypatch.setattr(tools, "git", _AddFails())
    with pytest.raises(git.GitError, match="index locked"):
        git.working_tree_id(clone)


def test_remote_listings_answer_empty_on_output_git_never_gives(
    repo: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    clone, _ = repo
    monkeypatch.setattr(_ops, "run", lambda *_: "")
    assert git.remote_tags(clone) == ()
    assert git.remote_branches(clone, "x/") == ()
    # A listing line naming no ref is passed over.
    monkeypatch.setattr(_ops, "run", lambda *_: "abc\tHEAD\n\t\n")
    assert git.remote_tags(clone) == ()
    assert git.remote_branches(clone, "x/") == ()


# --- where the checkout stands ----------------------------------------------


def test_the_position_is_one_process_and_detached_has_no_branch(
    repo: tuple[Path, Path],
) -> None:
    clone, _ = repo
    with _counting() as spawned:
        branch, head = git.position(clone)
    assert spawned["git"] == 0
    assert (branch, head) == (git.current_branch(clone), git.head_sha(clone))
    assert branch == "main" and len(head) == 40
    git.detach(clone, "HEAD")
    assert git.position(clone) == ("", head)
    assert git.current_branch(clone) == ""
    git.switch(clone, "main")
    assert git.current_branch(clone) == "main"


def test_has_commits_is_false_before_the_first_commit(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    _git(empty, "init", "-q", "--initial-branch=main")
    assert not git.has_commits(empty)
    assert not git.tags(empty)
    assert git.unpushed(empty) == []
    assert git.sha_of(empty, "HEAD") == ""
    with pytest.raises(git.GitError, match="HEAD names no commit"):
        git.head_sha(empty)
    _identify(empty)
    _commit(empty, "a.txt", "a\n", "chore: first")
    assert git.has_commits(empty)


def test_the_main_checkout_is_the_same_from_a_worktree(
    repo: tuple[Path, Path], tmp_path: Path
) -> None:
    clone, _ = repo
    tree = tmp_path / "tree"
    _git(clone, "worktree", "add", "-q", str(tree), "-b", "feat/1-tree")
    assert git.main_checkout(tree).resolve() == clone.resolve()
    assert git.main_checkout(clone).resolve() == clone.resolve()


def test_the_head_message_subject_and_body_agree(repo: tuple[Path, Path]) -> None:
    clone, _ = repo
    _commit(clone, "b.txt", "b\n", "feat: subject\n\nthe body\nsecond line")
    assert git.head_subject(clone) == "feat: subject"
    assert git.head_body(clone) == "the body\nsecond line"
    assert git.head_message(clone) == ("feat: subject", "the body\nsecond line")
    assert git.commit_message(clone, "HEAD").strip() == (
        "feat: subject\n\nthe body\nsecond line"
    )


# --- objects and trees ------------------------------------------------------


def test_object_ids_read_a_file_a_directory_and_tolerate_an_absent_path(
    repo: tuple[Path, Path],
) -> None:
    clone, _ = repo
    (clone / "pkg").mkdir()
    _commit(clone, "pkg/x.txt", "x\n", "feat: pkg")
    assert git.object_ids(clone, "HEAD", []) == {}
    found = git.object_ids(clone, "HEAD", ["seed.txt", "pkg", "nonesuch"])
    assert set(found) == {"seed.txt", "pkg"}
    assert found["pkg"] == git.object_id(clone, "HEAD:pkg")
    with pytest.raises(git.GitError):
        git.object_id(clone, "HEAD:nonesuch")


def test_the_working_tree_id_is_heads_when_clean_and_moves_with_untracked_files(
    repo: tuple[Path, Path],
) -> None:
    clone, _ = repo
    head_tree = git.object_id(clone, "HEAD^{tree}")
    assert git.working_tree_id(clone) == head_tree
    (clone / ".gitignore").write_text("ignored.txt\n")
    _git(clone, "add", ".gitignore")
    _git(clone, "commit", "-q", "-m", "chore: ignore")
    clean = git.working_tree_id(clone)
    (clone / "ignored.txt").write_text("x\n")
    assert git.working_tree_id(clone) == clean
    (clone / "new.txt").write_text("new\n")
    dirty = git.working_tree_id(clone)
    assert dirty != clean
    assert git.is_clean(clone) is False
    assert git.dirty_paths(clone) == ["new.txt"]
    # The real index was never touched: nothing is staged.
    assert _git(clone, "diff", "--cached", "--name-only").strip() == ""
    # A repository without an index file yet reads the same way.
    fresh = clone.parent / "fresh"
    fresh.mkdir()
    _git(fresh, "init", "-q", "--initial-branch=main")
    (fresh / "a.txt").write_text("a\n")
    assert len(git.working_tree_id(fresh)) == 40


def test_tree_diffs_and_first_parent_trees(repo: tuple[Path, Path]) -> None:
    clone, _ = repo
    first = git.object_id(clone, "HEAD^{tree}")
    _commit(clone, "b.txt", "b\n", "feat: b")
    second = git.object_id(clone, "HEAD^{tree}")
    assert git.tree_diff(clone, first, second) == ["b.txt"]
    assert git.first_parent_trees(clone, 5) == [second, first]
    assert git.changed_between(clone, "HEAD~1", "HEAD", ("*.txt",)) == ["b.txt"]
    assert git.changed_between(clone, "HEAD~1", "HEAD", ("*.md",)) == []
    assert git.files_in_commit(clone, "HEAD") == ["b.txt"]
    assert git.file_at(clone, "HEAD", "b.txt") == "b\n"
    assert git.file_at(clone, "HEAD", "nonesuch") == ""
    assert git.file_at(clone, "HEAD", "") == ""  # the tree itself is no file
    assert git.last_commit_touching(clone, "b.txt") == git.head_sha(clone)
    assert git.last_commit_touching(clone, "nonesuch") == ""
    assert git.log_paths(clone, "HEAD~1..HEAD", ("b.txt",)) == ("feat: b",)
    assert git.recent_commits(clone, 1) == ((git.head_sha(clone), "feat: b"),)
    assert git.commits_since(clone, "HEAD~1") == 1


def test_sha_of_resolves_names_and_answers_empty_for_nothing(
    repo: tuple[Path, Path],
) -> None:
    clone, _ = repo
    head = git.head_sha(clone)
    assert git.sha_of(clone, "main") == head
    assert git.sha_of(clone, "HEAD") == head
    assert git.sha_of(clone, "nonesuch") == ""
    # The grammar's corners: a peel, an abbreviation, a reflog spelling.
    assert git.sha_of(clone, "HEAD^{commit}") == head
    assert git.sha_of(clone, head[:7]) == head
    assert git.sha_of(clone, "main@{0}") == head
    assert git.sha_of(clone, "0000000") == ""
    assert git.sha_of(clone, "HEAD~99") == ""
    assert git.sha_of(clone, "HEAD~x") == ""
    assert git.sha_of(clone, "~1") == ""
    assert git.sha_of(clone, git.object_id(clone, "HEAD^{tree}")) == ""
    assert git.object_ids(clone, "main@{0}", ["seed.txt"]) == git.object_ids(
        clone, "HEAD", ["seed.txt"]
    )
    with pytest.raises(git.GitError, match="names nothing"):
        git.object_id(clone, "nonesuch")


# --- the remote ---------------------------------------------------------------


def test_a_fetch_brings_a_colleagues_push_and_tags_need_the_tag_fetch(
    repo: tuple[Path, Path], tmp_path: Path
) -> None:
    clone, origin = repo
    other = _second_clone(tmp_path, origin)
    pushed = _commit(other, "o.txt", "o\n", "feat: other")
    _git(other, "push", "-q", "origin", "main")
    _git(other, "tag", "-a", "pkg/v1.0.0", "-m", "pkg/v1.0.0", "HEAD~1")
    _git(other, "push", "-q", "origin", "pkg/v1.0.0")
    assert git.remote_head(clone, "main") != pushed
    git.fetch(clone)
    assert git.remote_head(clone, "main") == pushed
    assert git.remote_head(clone, "nonesuch") == ""
    assert git.behind_base(clone, "main") == 1
    assert git.subjects_ahead(clone, "main") == []
    assert git.is_ancestor(clone, "HEAD", pushed)
    assert not git.is_ancestor(clone, pushed, "HEAD")
    assert not git.is_ancestor(clone, "0" * 40, "HEAD")
    git.fetch_tags(clone)
    assert git.tags(clone) == ("pkg/v1.0.0",)
    assert git.remote_tags(clone) == ("pkg/v1.0.0",)


def test_a_push_sets_upstream_and_a_leased_force_push_refuses_a_moved_origin(
    repo: tuple[Path, Path], tmp_path: Path
) -> None:
    clone, origin = repo
    git.create_branch(clone, "feat/1-work")
    _commit(clone, "w.txt", "w\n", "feat: work")
    git.push(clone, "feat/1-work")
    assert git.any_head(clone, "feat/1-work") == git.head_sha(clone)
    assert git.remote_branches(clone, "feat/") == ("feat/1-work",)
    assert git.tracking_branches(clone, "feat/") == ("feat/1-work",)
    assert git.local_branches(clone, "feat/") == ("feat/1-work",)
    # A symbolic ref pointing at a branch that is gone lists nothing.
    _git(clone, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/gone")
    assert git.tracking_branches(clone, "") == ("feat/1-work", "main")
    other = _second_clone(tmp_path, origin)
    _git(other, "switch", "-q", "feat/1-work")
    _commit(other, "o.txt", "o\n", "feat: moved")
    _git(other, "push", "-q", "origin", "feat/1-work")
    git.amend_all(clone)
    with pytest.raises(git.GitError, match="force-with-lease"):
        git.push_force(clone, "feat/1-work")
    git.fetch(clone)
    git.push_force(clone, "feat/1-work")
    assert git.remote_head(clone, "feat/1-work") == git.head_sha(clone)


def test_any_head_falls_back_to_the_remote_and_then_to_nothing(
    repo: tuple[Path, Path], tmp_path: Path
) -> None:
    clone, origin = repo
    other = _second_clone(tmp_path, origin)
    _git(other, "switch", "-q", "-c", "feat/2-remote")
    pushed = _commit(other, "r.txt", "r\n", "feat: remote only")
    _git(other, "push", "-q", "-u", "origin", "feat/2-remote")
    assert git.any_head(clone, "feat/2-remote") == pushed
    assert git.any_head(clone, "feat/3-nowhere") == ""


# --- merging and conflicts ----------------------------------------------------


def _conflicting_branches(clone: Path) -> None:
    git.create_branch(clone, "feat/1-ours")
    _commit(clone, "same.txt", "ours\n", "feat: ours")
    git.switch(clone, "main")
    _commit(clone, "same.txt", "theirs\n", "feat: theirs")
    _git(clone, "push", "-q", "origin", "main")
    git.switch(clone, "feat/1-ours")


def test_a_conflict_is_listed_with_each_sides_text_and_resolved_by_add(
    repo: tuple[Path, Path],
) -> None:
    clone, _ = repo
    _conflicting_branches(clone)
    assert git.conflicts_with_base(clone, "main")
    with pytest.raises(git.GitError, match=r"merge-tree|CONFLICT|conflict"):
        git.merge_tree(clone, "main", "feat/1-ours")
    with pytest.raises(git.GitError):
        git.integrate(clone, "main")
    assert git.unmerged_paths(clone) == ["same.txt"]
    assert git.staged_text(clone, "same.txt", 1) == ""
    assert git.staged_text(clone, "same.txt", 2) == "ours\n"
    assert git.staged_text(clone, "same.txt", 3) == "theirs\n"
    (clone / "same.txt").write_text("both\n")
    git.add(clone, "same.txt")
    assert git.unmerged_paths(clone) == []
    _git(clone, "-c", "core.editor=true", "commit", "-q", "--no-edit")
    assert git.merge_parents(clone) is not None


def test_a_clean_merge_tree_writes_a_tree_and_integrate_merges(
    repo: tuple[Path, Path],
) -> None:
    clone, _ = repo
    git.create_branch(clone, "feat/1-clean")
    _commit(clone, "mine.txt", "mine\n", "feat: mine")
    git.switch(clone, "main")
    _commit(clone, "theirs.txt", "theirs\n", "feat: theirs")
    _git(clone, "push", "-q", "origin", "main")
    git.switch(clone, "feat/1-clean")
    merged = git.merge_tree(clone, "main", "feat/1-clean")
    assert len(merged) == 40
    assert git.merge_parents(clone) is None
    git.integrate(clone, "main")
    parents = git.merge_parents(clone)
    assert parents is not None
    assert parents[0] == git.sha_of(clone, "HEAD~1")
    base = git.merge_base(clone, "main")
    assert base == git.remote_head(clone, "main")


def test_the_merge_base_raises_without_shared_history(
    repo: tuple[Path, Path],
) -> None:
    clone, _ = repo
    _git(clone, "checkout", "-q", "--orphan", "orphan")
    _git(clone, "rm", "-rfq", ".")
    _commit(clone, "alone.txt", "alone\n", "chore: orphan")
    with pytest.raises(git.GitError):
        git.merge_base(clone, "main")


def test_changed_paths_count_commits_renames_and_untracked_files(
    repo: tuple[Path, Path],
) -> None:
    clone, _ = repo
    git.create_branch(clone, "feat/1-paths")
    _commit(clone, "a.txt", "a\n", "feat: a")
    _git(clone, "mv", "a.txt", "b.txt")
    (clone / "pkg").mkdir()
    (clone / "pkg" / "deep.txt").write_text("deep\n")
    assert git.changed_paths(clone, "main") == ["a.txt", "b.txt", "pkg/deep.txt"]


# --- branches and config ----------------------------------------------------


def test_branches_are_created_switched_and_deleted(repo: tuple[Path, Path]) -> None:
    clone, _ = repo
    git.create_branch(clone, "feat/1-gone")
    assert git.current_branch(clone) == "feat/1-gone"
    git.switch(clone, "main")
    git.delete_branch(clone, "feat/1-gone")
    assert not git.local_branch_exists(clone, "feat/1-gone")
    git.commit_all(clone, "chore: nothing staged yet") if False else None
    (clone / "c.txt").write_text("c\n")
    git.commit_all(clone, "feat: c")
    assert git.head_subject(clone) == "feat: c"


def test_config_reads_writes_lists_and_unsets(repo: tuple[Path, Path]) -> None:
    clone, _ = repo
    assert git.config_get(clone, "branch.main.workshop-parent") == ""
    assert git.config_get(clone, "nodot") == ""
    assert git.config_get(clone.parent, "user.name") == ""  # no repository there
    git.config_set(clone, "branch.main.workshop-parent", "feat/0")
    git.config_set(clone, "branch.main.workshop-parent-tip", "abc")
    assert git.config_get(clone, "branch.main.workshop-parent") == "feat/0"
    assert git.config_regexp(clone, r"^branch\.main\.workshop-parent") == {
        "branch.main.workshop-parent": "feat/0",
        "branch.main.workshop-parent-tip": "abc",
    }
    assert git.config_regexp(clone, r"^nonesuch\.") == {}
    git.config_unset(clone, "branch.main.workshop-parent")
    git.config_unset(clone, "branch.main.workshop-parent")  # already gone
    assert git.config_get(clone, "branch.main.workshop-parent") == ""


def test_the_author_email_is_what_git_resolves_environment_first(
    repo: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    clone, _ = repo
    assert git.author_email(clone) == "tester@example.invalid"
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "rider@example.invalid")
    assert git.author_email(clone) == "rider@example.invalid"


# --- the functions the workshop's direct calls converged on ---------------------


def test_fetch_prunes_forces_and_takes_a_tag(
    repo: tuple[Path, Path], tmp_path: Path
) -> None:
    clone, origin = repo
    other = _second_clone(tmp_path, origin)
    _git(other, "switch", "-q", "-c", "feat/9-gone")
    _commit(other, "g.txt", "g\n", "feat: gone")
    _git(other, "push", "-q", "-u", "origin", "feat/9-gone")
    _git(other, "tag", "-a", "v1", "-m", "v1")
    _git(other, "push", "-q", "origin", "v1")
    git.fetch(clone, prune=True)
    assert "feat/9-gone" in git.tracking_branches(clone, "feat/")
    _git(other, "push", "-q", "origin", "--delete", "feat/9-gone")
    git.fetch(clone, prune=True)
    assert "feat/9-gone" not in git.tracking_branches(clone, "feat/")
    git.fetch_tag(clone, "v1")
    assert "v1" in git.tags(clone)
    # The tag recut elsewhere replaces the one held here under force.
    _git(other, "tag", "-f", "-a", "v1", "-m", "v1 again", "HEAD~1")
    _git(other, "push", "-q", "-f", "origin", "v1")
    git.fetch_tag(clone, "v1")
    assert git.sha_of(clone, "v1") == git.sha_of(clone, "HEAD")
    git.fetch(clone, "main", force=True)


def test_a_branch_is_recreated_reset_and_the_tree_cleaned(
    repo: tuple[Path, Path],
) -> None:
    clone, _ = repo
    head = git.head_sha(clone)
    git.switch_new(clone, "chore/x")
    _commit(clone, "x.txt", "x\n", "chore: x")
    git.switch_new(clone, "chore/x", head)
    assert git.head_sha(clone) == head
    _commit(clone, "y.txt", "y\n", "chore: y")
    git.reset_hard(clone, head)
    assert not (clone / "y.txt").exists()
    (clone / "loose.txt").write_text("l\n")
    (clone / "kept.txt").write_text("k\n")
    assert git.clean(clone, keep=["kept.txt"], dry_run=True) == ["loose.txt"]
    assert (clone / "loose.txt").exists()
    assert git.clean(clone, keep=["kept.txt"]) == ["loose.txt"]
    assert not (clone / "loose.txt").exists() and (clone / "kept.txt").exists()
    assert git.clean(clone) == ["kept.txt"]


def test_lines_of_a_span_and_the_unpushed_commits(repo: tuple[Path, Path]) -> None:
    clone, _ = repo
    assert git.unpushed(clone) == []
    _commit(clone, "u.txt", "u\n", "feat: unpushed")
    assert [line.split(" ", 1)[1] for line in git.unpushed(clone)] == ["feat: unpushed"]
    assert [
        line.split(" ", 1)[1] for line in git.oneline(clone, "origin/main..main")
    ] == ["feat: unpushed"]
    assert git.oneline(clone, "main..origin/main") == []


def test_ls_remote_reads_heads_tags_and_a_url(repo: tuple[Path, Path]) -> None:
    clone, origin = repo
    _git(clone, "tag", "-a", "pkg/v1.0.0", "-m", "pkg/v1.0.0")
    _git(clone, "push", "-q", "origin", "pkg/v1.0.0")
    head = git.head_sha(clone)
    assert git.ls_remote(clone, "refs/heads/main") == {"refs/heads/main": head}
    # An annotated tag lists as its own object; the peeled entry is left out.
    assert git.ls_remote(clone, tags=True) == {
        "refs/tags/pkg/v1.0.0": git.object_id(clone, "pkg/v1.0.0")
    }
    assert git.ls_remote(clone, "refs/heads/main", remote=str(origin)) == {
        "refs/heads/main": head
    }
    assert git.ls_remote(clone, "refs/heads/nonesuch") == {}


def test_a_push_names_its_remote_forces_and_a_refspec_pushes_or_deletes_a_tag(
    repo: tuple[Path, Path], tmp_path: Path
) -> None:
    clone, origin = repo
    other = _second_clone(tmp_path, origin)
    _commit(other, "o.txt", "o\n", "feat: other")
    _git(other, "push", "-q", "origin", "main")
    _commit(clone, "c.txt", "c\n", "feat: mine")
    with pytest.raises(git.GitError):
        git.push(clone, "main")
    git.push(clone, "main", remote="origin", force=True)
    assert git.ls_remote(clone, "refs/heads/main")["refs/heads/main"] == git.head_sha(
        clone
    )
    _git(clone, "tag", "-a", "t1", "-m", "t1")
    git.push_refspec(clone, "refs/tags/t1")
    assert "refs/tags/t1" in git.ls_remote(clone, tags=True)
    git.push_refspec(clone, ":refs/tags/t1")
    assert "refs/tags/t1" not in git.ls_remote(clone, tags=True)


def test_a_worktree_is_added_detached_and_removed(
    repo: tuple[Path, Path], tmp_path: Path
) -> None:
    clone, _ = repo
    tree = tmp_path / "tree"
    git.worktree_add(clone, tree, "main", detach=True)
    assert (tree / "seed.txt").is_file() and git.current_branch(tree) == ""
    git.worktree_remove(clone, tree)
    assert not tree.exists()
    _git(clone, "branch", "feat/wt")
    git.worktree_add(clone, tree, "feat/wt")
    assert git.current_branch(tree) == "feat/wt"
    git.worktree_remove(clone, tree)
    with pytest.raises(git.GitError):
        git.worktree_add(clone, tree, "main")  # main is checked out here already


def test_restore_tracks_and_unstage(repo: tuple[Path, Path]) -> None:
    clone, _ = repo
    (clone / "seed.txt").write_text("changed\n")
    assert git.restore(clone, "seed.txt") == ""
    assert (clone / "seed.txt").read_text() == "seed\n"
    assert "nonesuch" in git.restore(clone, "nonesuch")
    assert git.tracks(clone, "HEAD:seed.txt")
    assert not git.tracks(clone, "HEAD:nonesuch")
    assert not git.tracks(clone, "nonesuch:seed.txt")
    assert git.file_at(clone, "nonesuch", "seed.txt") == ""
    (clone / "new.txt").write_text("n\n")
    _git(clone, "add", "new.txt")
    git.unstage(clone, "new.txt")
    git.unstage(clone, "never-added.txt")
    assert git.dirty_paths(clone) == ["new.txt"]
    assert "new.txt" in _git(clone, "status", "--porcelain")


def test_ls_files_lists_the_index_the_others_and_a_path(
    repo: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    clone, _ = repo
    (clone / "pkg").mkdir()
    (clone / "pkg" / "a.txt").write_text("a\n")
    (clone / ".gitignore").write_text("ignored.txt\n")
    (clone / "ignored.txt").write_text("i\n")
    assert git.ls_files(clone) == ["seed.txt"]
    assert git.ls_files(clone, others=True) == [".gitignore", "pkg/a.txt", "seed.txt"]
    assert git.ls_files(clone, "pkg", others=True, cached=False) == ["pkg/a.txt"]
    monkeypatch.setattr(tools, "git", _Failing(128, "fatal: not a git repository"))
    with pytest.raises(git.GitError, match="not a git repository"):
        git.ls_files(clone)


def test_remotes_are_read_added_and_repointed(
    repo: tuple[Path, Path], tmp_path: Path
) -> None:
    clone, origin = repo
    assert git.remote_url(clone) == str(origin)
    assert git.remotes(clone) == ("origin",)
    with pytest.raises(git.GitError):
        git.remote_url(clone, "nonesuch")
    git.add_remote(clone, "mirror", str(tmp_path / "mirror.git"))
    git.set_remote_url(clone, "mirror", str(tmp_path / "elsewhere.git"))
    assert git.remote_url(clone, "mirror") == str(tmp_path / "elsewhere.git")
    assert git.remotes(clone) == ("mirror", "origin")


def test_tag_dates_pair_each_tag_with_its_day(repo: tuple[Path, Path]) -> None:
    clone, _ = repo
    _git(clone, "tag", "-a", "packages/x/v0.1.0", "-m", "x")
    _git(clone, "tag", "packages/y/v0.2.0")
    _git(clone, "tag", "packagesx/v0.3.0")
    dated = dict(git.tag_dates(clone, "refs/tags/packages"))
    assert set(dated) == {"packages/x/v0.1.0", "packages/y/v0.2.0"}
    assert all(len(day) == 10 for day in dated.values())
    assert git.tag_dates(clone, "refs/tags/nonesuch") == []
    assert [tag for tag, _ in git.tag_dates(clone, "refs/tags/")] == [
        "packages/x/v0.1.0",
        "packages/y/v0.2.0",
        "packagesx/v0.3.0",
    ]
