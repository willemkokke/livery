"""The workshop's sweep: refusals first, then what goes."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from livery.workshop import _sweep
from workshop_seeds import Seeds, _seed_home, pushed, seed_copier  # noqa: F401


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def _build(base: Path) -> None:
    # Named for the checkout it stands in for: every message this suite
    # asserts on prints the worktree's path under the checkout's name.
    pushed(base, clone="livery")


@pytest.fixture
def checkout(seeds: Seeds) -> Path:
    """A checkout with an origin, the base of every linked worktree."""
    return seeds("livery", _build) / "livery"


def _worktree(checkout: Path, home: Path, number: int, *, push: bool = True) -> Path:
    branch = f"feat/{number}-thing"
    tree = home / checkout.name / f"{number}-thing"
    tree.parent.mkdir(parents=True, exist_ok=True)
    _git(checkout, "worktree", "add", "-q", "-b", branch, str(tree), "main")
    (tree / f"{number}.txt").write_text("work\n")
    _git(tree, "add", ".")
    _git(tree, "commit", "-qm", f"feat: {number}")
    if push:
        _git(tree, "push", "-q", "-u", "origin", branch)
    return tree


def _forge(
    *,
    closed: frozenset[int] = frozenset(),
    merged: frozenset[str] = frozenset(),
    heads: dict[str, str] | None = None,
    cleared: frozenset[str] = frozenset(),
    opened: frozenset[str] = frozenset(),
    broken: bool = False,
) -> Any:
    """A forge knowing closed issues and merged branches, and the merged heads.

    A merged branch in *cleared* is one whose pull request the forge
    no longer files under its head branch: only its head sha finds it,
    and its head branch reads empty. A branch in *opened* heads the
    open PR #11.
    """
    merged_heads: dict[str, str] = heads or {}

    class _Issues:
        def get(self, number: int) -> Any:
            if broken:
                raise RuntimeError("the forge is down")
            return SimpleNamespace(state="closed" if number in closed else "open")

    class _Pulls:
        def find_by_head(self, branch: str, *, state: str = "open") -> Any:
            if broken:
                raise RuntimeError("the forge is down")
            if branch in merged and branch not in cleared:
                return SimpleNamespace(
                    number=9,
                    merged=True,
                    head_sha=merged_heads.get(branch, ""),
                    head_branch=branch,
                )
            if branch in opened:
                return SimpleNamespace(
                    number=11,
                    merged=False,
                    state="open",
                    head_sha="",
                    head_branch=branch,
                )
            return None

        def find_by_head_sha(self, sha: str) -> Any:
            if broken:
                raise RuntimeError("the forge is down")
            for branch, head in merged_heads.items():
                if head == sha and branch in merged:
                    return SimpleNamespace(
                        number=9,
                        merged=True,
                        head_sha=head,
                        head_branch="" if branch in cleared else branch,
                    )
            return None

    return SimpleNamespace(issue=_Issues(), pr=_Pulls())


def test_a_tree_holding_work_or_an_open_issue_stays_and_is_named(
    checkout: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home" / "worktrees"
    dirty = _worktree(checkout, home, 1)
    (dirty / "dirt.txt").write_text("dirt\n")
    unpushed = _worktree(checkout, home, 2, push=False)
    open_issue = _worktree(checkout, home, 3)
    monkeypatch.setattr(
        _sweep, "_repository", lambda tree: _forge(closed=frozenset({1, 2}))
    )
    lines = _sweep.sweep_worktrees(home, dry_run=False)
    assert (
        "worktree livery/1-thing: issue #1 is closed, but it holds uncommitted"
        " changes; kept" in lines
    )
    assert any(
        line.startswith("worktree livery/2-thing: issue #2 is closed, but it holds")
        and line.endswith("kept")
        for line in lines
    )
    assert "worktree livery/3-thing: issue #3 is open; kept" in lines
    assert dirty.is_dir() and unpushed.is_dir() and open_issue.is_dir()
    # The forge down: everything stays, and the reason is the forge's.
    monkeypatch.setattr(_sweep, "_repository", lambda tree: _forge(broken=True))
    lines = _sweep.sweep_worktrees(home, dry_run=False)
    assert lines and all(
        "the forge could not be asked" in line and line.endswith("kept")
        for line in lines
    )


def test_a_closed_issues_tree_stays_while_its_branch_heads_an_open_pr(
    checkout: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # fm issue.close keeps such a tree for its pull request; the sweep
    # that follows any fm command must not take it after all.
    home = tmp_path / "home" / "worktrees"
    tree = _worktree(checkout, home, 8)
    monkeypatch.setattr(
        _sweep,
        "_repository",
        lambda t: _forge(closed=frozenset({8}), opened=frozenset({"feat/8-thing"})),
    )
    lines = _sweep.sweep_worktrees(home, dry_run=False)
    assert lines == [
        "worktree livery/8-thing: issue #8 is closed, but PR #11 is open from"
        " its branch; kept"
    ]
    assert tree.is_dir()


def test_a_forge_that_cannot_be_asked_keeps_the_tree_and_says_why(
    checkout: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home" / "worktrees"
    closed = _worktree(checkout, home, 4)
    gone = home / checkout.name / "5-thing"
    gone.mkdir()
    (gone / ".git").write_text("gitdir: /nowhere/.git/worktrees/5-thing\n")
    (home / checkout.name / "notes").mkdir()

    def _down(tree: Path) -> Any:
        raise RuntimeError("the forge is down")

    monkeypatch.setattr(_sweep, "_repository", _down)
    lines = _sweep.sweep_worktrees(home, dry_run=False)
    # The rule that needs no forge runs wherever it is called from.
    assert "worktree livery/5-thing: its checkout is gone; removed" in lines
    assert not gone.exists()
    assert "worktree livery/notes: not a linked worktree; kept" in lines
    # The rule that does need it keeps the tree and names the reason.
    assert closed.is_dir()
    assert any("4-thing" in line and "kept" in line for line in lines)


def test_a_closed_clean_pushed_tree_goes_through_its_checkouts_git(
    checkout: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home" / "worktrees"
    tree = _worktree(checkout, home, 6)
    merged = _worktree(checkout, home, 7)
    monkeypatch.setattr(
        _sweep,
        "_repository",
        lambda t: _forge(closed=frozenset({6}), merged=frozenset({"feat/7-thing"})),
    )
    lines = _sweep.sweep_worktrees(home, dry_run=False)
    assert (
        "worktree livery/6-thing: issue #6 is closed, nothing only here; removed"
        in lines
    )
    assert "worktree livery/7-thing: PR #9 merged, nothing only here; removed" in lines
    assert not tree.exists() and not merged.exists()
    assert "6-thing" not in _git(checkout, "worktree", "list")
    # Idempotent: nothing left to sweep.
    assert _sweep.sweep_worktrees(home, dry_run=False) == []


def test_the_store_is_swept_in_the_checkouts_local_scope(
    checkout: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # On a desk and on a runner alike: the test's checkout is a machine's.
    monkeypatch.setattr("livery.workshop._ci_context.run_context", lambda: None)
    facts: dict[str, Any] = {
        "data_dir": tmp_path / "data",
        "cache_dir": tmp_path / "cache",
        "config_dir": tmp_path / "config",
        "now": datetime.now(UTC),
        "root": checkout,
    }
    lines = _sweep.sweep(dry_run=False, **facts)
    for name in ("gate-record", "diagnostics"):
        assert (
            f"state store: refs/workshop-local/{name}: 0 row(s), within its bounds"
            in lines
        )
    assert "state store: remote series: swept inside CI, never from a machine" in lines


def test_the_rest_of_the_data_directory_is_bounded_or_reported(tmp_path: Path) -> None:
    data = tmp_path / "data"
    config = tmp_path / "config"
    config.mkdir()
    (config / "config.toml").write_text("")
    (config / "birth-e2e").mkdir()
    loop = data / "workshop-e2e"
    loop.mkdir(parents=True)
    (loop / "big.bin").write_bytes(b"x" * 2_000_000)
    facts: dict[str, Any] = {
        "data_dir": data,
        "cache_dir": tmp_path / "cache",
        "config_dir": config,
        "now": datetime.now(UTC),
        "root": None,
    }
    # A dry run first: it says, and changes nothing.
    said = _sweep.sweep(dry_run=True, **facts)
    assert "state store: no checkout here; nothing swept" in said
    # Then for real: the config directory reported only, the loop kept.
    done = _sweep.sweep(dry_run=False, **facts)
    assert (
        "config: birth-e2e is not the config, the tasks file, or the shared env;"
        " yours to remove" in done
    )
    assert (config / "birth-e2e").is_dir()
    assert any(
        line.startswith("loop workspace: 2 MB") and "kept" in line for line in done
    )
    assert loop.is_dir()
    # A second sweep still reports.
    again = _sweep.sweep(dry_run=False, **facts)
    assert any(line.startswith("config: birth-e2e") for line in again)
    assert any(line.startswith("loop workspace:") for line in again)


def _squash_onto_main(checkout: Path, branch: str) -> None:
    """Squash *branch* onto main, push, and delete the remote branch, forge-style."""
    _git(checkout, "switch", "-q", "main")
    _git(checkout, "merge", "-q", "--squash", branch)
    _git(checkout, "commit", "-qm", f"squash of {branch}")
    _git(checkout, "push", "-q", "origin", "main")
    _git(checkout, "push", "-q", "origin", "--delete", branch)


def test_a_stacked_tree_at_its_parents_tip_stays_when_the_parent_merges(
    checkout: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A branch started on another with no commit of its own has the
    # parent's head, so the parent's merged pull request answers its
    # sha; it is not the child's, whether the forge keeps the parent's
    # head branch on it or cleared it.
    from livery.workshop import _stack

    home = tmp_path / "home" / "worktrees"
    parent = _worktree(checkout, home, 1)
    tip = _git(parent, "rev-parse", "HEAD")
    child = home / checkout.name / "2-thing"
    _git(
        checkout,
        "worktree",
        "add",
        "-q",
        "-b",
        "feat/2-thing",
        str(child),
        "feat/1-thing",
    )
    _stack.record_stack(checkout, "feat/2-thing", "feat/1-thing", tip)
    _squash_onto_main(checkout, "feat/1-thing")
    kept = _forge(
        closed=frozenset({1}),
        merged=frozenset({"feat/1-thing"}),
        heads={"feat/1-thing": tip},
    )
    monkeypatch.setattr(_sweep, "_repository", lambda _tree: kept)
    lines = _sweep.sweep_worktrees(home, dry_run=True)
    assert "worktree livery/2-thing: issue #2 is open; kept" in lines
    cleared = _forge(
        closed=frozenset({1}),
        merged=frozenset({"feat/1-thing"}),
        heads={"feat/1-thing": tip},
        cleared=frozenset({"feat/1-thing"}),
    )
    monkeypatch.setattr(_sweep, "_repository", lambda _tree: cleared)
    lines = _sweep.sweep_worktrees(home, dry_run=False)
    assert "worktree livery/2-thing: issue #2 is open; kept" in lines
    assert child.is_dir()
    # Its own commit makes the head its own: still open, still kept.
    (child / "2.txt").write_text("work\n")
    _git(child, "add", ".")
    _git(child, "commit", "-qm", "feat: 2")
    lines = _sweep.sweep_worktrees(home, dry_run=False)
    assert "worktree livery/2-thing: issue #2 is open; kept" in lines


def test_a_tree_whose_merged_pull_request_lost_its_head_branch_goes_by_its_head(
    checkout: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home" / "worktrees"
    tree = _worktree(checkout, home, 5)
    tip = _git(tree, "rev-parse", "HEAD")
    _squash_onto_main(checkout, "feat/5-thing")
    forge = _forge(
        merged=frozenset({"feat/5-thing"}),
        heads={"feat/5-thing": tip},
        cleared=frozenset({"feat/5-thing"}),
    )
    monkeypatch.setattr(_sweep, "_repository", lambda _tree: forge)
    lines = _sweep.sweep_worktrees(home, dry_run=False)
    assert "worktree livery/5-thing: PR #9 merged, nothing only here; removed" in lines
    assert not tree.exists()


def test_a_squash_merged_tree_is_judged_by_its_merged_head(
    checkout: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home" / "worktrees"
    tree = _worktree(checkout, home, 8)
    tip = _git(tree, "rev-parse", "HEAD")
    _squash_onto_main(checkout, "feat/8-thing")
    # Past the merge: unique work, kept and named.
    (tree / "after.txt").write_text("a\n")
    _git(tree, "add", ".")
    _git(tree, "commit", "-qm", "feat: after the merge")
    monkeypatch.setattr(
        _sweep,
        "_repository",
        lambda _tree: _forge(
            merged=frozenset({"feat/8-thing"}), heads={"feat/8-thing": tip}
        ),
    )
    lines = _sweep.sweep_worktrees(home, dry_run=False)
    assert (
        "worktree livery/8-thing: PR #9 merged, but it holds 1 commit(s) past the"
        " merged head; kept" in lines
    )
    assert tree.is_dir()
    # At the merged head: nothing only here, whatever the squash did
    # to the ancestry (the branch's commit is no ancestor of main).
    _git(tree, "reset", "-q", "--hard", tip)
    lines = _sweep.sweep_worktrees(home, dry_run=False)
    assert "worktree livery/8-thing: PR #9 merged, nothing only here; removed" in lines
    assert not tree.exists()


def test_a_tree_named_after_no_issue_is_judged_by_its_pull_request(
    checkout: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home" / "worktrees"
    tree = home / checkout.name / "docs-thing"
    tree.parent.mkdir(parents=True, exist_ok=True)
    _git(checkout, "worktree", "add", "-q", "-b", "docs/thing", str(tree), "main")
    (tree / "note.md").write_text("n\n")
    _git(tree, "add", ".")
    _git(tree, "commit", "-qm", "docs: a note")
    _git(tree, "push", "-q", "-u", "origin", "docs/thing")
    tip = _git(tree, "rev-parse", "HEAD")
    monkeypatch.setattr(_sweep, "_repository", lambda _tree: _forge())
    lines = _sweep.sweep_worktrees(home, dry_run=False)
    assert "worktree livery/docs-thing: no merged pull request; kept" in lines
    assert tree.is_dir()
    _squash_onto_main(checkout, "docs/thing")
    monkeypatch.setattr(
        _sweep,
        "_repository",
        lambda _tree: _forge(
            merged=frozenset({"docs/thing"}), heads={"docs/thing": tip}
        ),
    )
    lines = _sweep.sweep_worktrees(home, dry_run=False)
    assert (
        "worktree livery/docs-thing: PR #9 merged, nothing only here; removed" in lines
    )
    assert not tree.exists()


def test_merged_local_branches_of_the_checkout_are_swept(
    checkout: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _branch(name: str) -> str:
        _git(checkout, "switch", "-q", "-c", name, "main")
        (checkout / f"{name.replace('/', '-')}.txt").write_text("x\n")
        _git(checkout, "add", ".")
        _git(checkout, "commit", "-qm", f"docs: {name}")
        _git(checkout, "push", "-q", "-u", "origin", name)
        return _git(checkout, "rev-parse", "HEAD")

    one = _branch("docs/one")
    _squash_onto_main(checkout, "docs/one")
    two = _branch("docs/two")
    _squash_onto_main(checkout, "docs/two")
    _git(checkout, "switch", "-q", "docs/two")
    (checkout / "after.txt").write_text("a\n")
    _git(checkout, "add", ".")
    _git(checkout, "commit", "-qm", "docs: after the merge")
    _branch("feat/3-open")
    _git(checkout, "switch", "-q", "main")
    merged = frozenset({"docs/one", "docs/two"})
    heads = {"docs/one": one, "docs/two": two}
    monkeypatch.setattr(
        _sweep, "_repository", lambda _root: _forge(merged=merged, heads=heads)
    )
    lines = _sweep.sweep_branches(checkout, dry_run=True)
    assert "branch docs/one: PR #9 merged, nothing only here; would remove" in lines
    assert (
        "branch docs/two: PR #9 merged, but it holds 1 commit(s) past the merged"
        " head; kept" in lines
    )
    assert "branch feat/3-open: no merged pull request; kept" in lines
    assert "docs/one" in _git(checkout, "branch", "--list", "docs/one")  # a dry run
    # Standing on the merged branch: named for sync, never moved.
    _git(checkout, "switch", "-q", "docs/one")
    lines = _sweep.sweep_branches(checkout, dry_run=False)
    assert (
        "branch docs/one: PR #9 merged, but the checkout stands on it;"
        " `fm sync` steps off it" in lines
    )
    _git(checkout, "switch", "-q", "main")
    lines = _sweep.sweep_branches(checkout, dry_run=False)
    assert "branch docs/one: PR #9 merged, nothing only here; removed" in lines
    assert _git(checkout, "branch", "--list", "docs/one") == ""
    assert "docs/two" in _git(checkout, "branch", "--list", "docs/two")
    # The forge down: every branch stays, and the reason is the forge's.
    monkeypatch.setattr(_sweep, "_repository", lambda _root: _forge(broken=True))

    lines = _sweep.sweep_branches(checkout, dry_run=False)
    assert lines and all(
        "the forge could not be asked" in line and line.endswith("kept")
        for line in lines
    )
