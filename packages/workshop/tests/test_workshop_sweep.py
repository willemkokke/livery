"""The workshop's sweep: refusals first, then what goes."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from livery.workshop import _sweep


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    """A checkout with an origin, the base of every linked worktree."""
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "--initial-branch=main", str(origin))
    root = tmp_path / "livery"
    _git(tmp_path, "clone", "-q", str(origin), str(root))
    _git(root, "config", "user.name", "T")
    _git(root, "config", "user.email", "t@livery.local")
    (root / "seed.txt").write_text("seed\n")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "chore: seed")
    _git(root, "push", "-q", "-u", "origin", "main")
    return root


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
    broken: bool = False,
) -> Any:
    """A forge knowing closed issues and merged branches, and the merged heads."""
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
            if branch in merged:
                return SimpleNamespace(
                    number=9, merged=True, head_sha=merged_heads.get(branch, "")
                )
            return None

        def find_by_head_sha(self, sha: str) -> Any:
            if broken:
                raise RuntimeError("the forge is down")
            for branch, head in merged_heads.items():
                if head == sha and branch in merged:
                    return SimpleNamespace(number=9, merged=True, head_sha=head)
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
    lines = _sweep.sweep_worktrees(home, dry_run=False, unattended=False)
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
    lines = _sweep.sweep_worktrees(home, dry_run=False, unattended=False)
    assert lines and all(
        "the forge could not be asked" in line and line.endswith("kept")
        for line in lines
    )


def test_unattended_never_asks_the_forge_and_removes_only_the_gone(
    checkout: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home" / "worktrees"
    closed = _worktree(checkout, home, 4)
    gone = home / checkout.name / "5-thing"
    gone.mkdir()
    (gone / ".git").write_text("gitdir: /nowhere/.git/worktrees/5-thing\n")
    (home / checkout.name / "notes").mkdir()

    def _never(tree: Path) -> Any:
        raise AssertionError("the forge was asked unattended")

    monkeypatch.setattr(_sweep, "_repository", _never)
    lines = _sweep.sweep_worktrees(home, dry_run=True, unattended=True)
    assert "worktree livery/5-thing: its checkout is gone; would remove" in lines
    assert gone.is_dir()  # a dry run removes nothing
    lines = _sweep.sweep_worktrees(home, dry_run=False, unattended=True)
    assert "worktree livery/5-thing: its checkout is gone; removed" in lines
    assert not gone.exists() and closed.is_dir()
    assert "worktree livery/notes: not a linked worktree; kept" in lines


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
    lines = _sweep.sweep_worktrees(home, dry_run=False, unattended=False)
    assert (
        "worktree livery/6-thing: issue #6 is closed, nothing only here; removed"
        in lines
    )
    assert "worktree livery/7-thing: PR #9 merged, nothing only here; removed" in lines
    assert not tree.exists() and not merged.exists()
    assert "6-thing" not in _git(checkout, "worktree", "list")
    # Idempotent: nothing left to sweep.
    assert _sweep.sweep_worktrees(home, dry_run=False, unattended=False) == []


def test_the_store_is_swept_in_the_checkouts_local_scope(
    checkout: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # On a desk and on a runner alike: the test's checkout is a machine's.
    monkeypatch.setattr("livery.workshop._state.run_context", lambda: None)
    facts: dict[str, Any] = {
        "data_dir": tmp_path / "data",
        "cache_dir": tmp_path / "cache",
        "config_dir": tmp_path / "config",
        "now": datetime.now(UTC),
        "root": checkout,
    }
    lines = _sweep.sweep(dry_run=False, unattended=True, **facts)
    for name in ("gate-record", "diagnostics"):
        assert (
            f"state store: refs/workshop-local/{name}: 0 row(s), within its bounds"
            in lines
        )
    assert "state store: remote series: swept inside CI, never from a machine" in lines


def test_the_rest_of_the_data_directory_is_bounded_or_reported(tmp_path: Path) -> None:
    data = tmp_path / "data"
    # The homes of rows the workshop no longer keeps on the machine.
    (data / "diagnostics").mkdir(parents=True)
    (data / "diagnostics" / "20260901T000000Z-x.json").write_text("{}")
    (data / "livery-workshop").mkdir()
    (data / "livery-workshop" / "gate-record.json").write_text("[]")
    (data / "checkouts.txt").write_text("/gone/path\n")
    config = tmp_path / "config"
    config.mkdir()
    (config / "config.toml").write_text("")
    (config / "birth-e2e").mkdir()
    loop = data / "workshop-e2e"
    loop.mkdir()
    (loop / "big.bin").write_bytes(b"x" * 2_000_000)
    facts: dict[str, Any] = {
        "data_dir": data,
        "cache_dir": tmp_path / "cache",
        "config_dir": config,
        "now": datetime.now(UTC),
        "root": None,
    }
    # A dry run first: it says, and changes nothing.
    said = _sweep.sweep(dry_run=True, unattended=False, **facts)
    assert "state store: no checkout here; nothing swept" in said
    for name in ("checkouts.txt", "diagnostics", "livery-workshop"):
        assert f"{name}: no code writes it any more; would remove" in said
        assert (data / name).exists()
    # Then for real: gone, the config directory reported only, the loop kept.
    done = _sweep.sweep(dry_run=False, unattended=False, **facts)
    for name in ("checkouts.txt", "diagnostics", "livery-workshop"):
        assert f"{name}: no code writes it any more; removed" in done
        assert not (data / name).exists()
    assert (
        "config: birth-e2e is not the config, the tasks file, or the shared env;"
        " yours to remove" in done
    )
    assert (config / "birth-e2e").is_dir()
    assert any(
        line.startswith("loop workspace: 2 MB") and "kept" in line for line in done
    )
    assert loop.is_dir()
    # Unattended, the reports are left to a person.
    quiet = _sweep.sweep(dry_run=False, unattended=True, **facts)
    assert not any(line.startswith(("config:", "loop workspace:")) for line in quiet)


def _squash_onto_main(checkout: Path, branch: str) -> None:
    """Squash *branch* onto main, push, and delete the remote branch, forge-style."""
    _git(checkout, "switch", "-q", "main")
    _git(checkout, "merge", "-q", "--squash", branch)
    _git(checkout, "commit", "-qm", f"squash of {branch}")
    _git(checkout, "push", "-q", "origin", "main")
    _git(checkout, "push", "-q", "origin", "--delete", branch)


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
    lines = _sweep.sweep_worktrees(home, dry_run=False, unattended=False)
    assert (
        "worktree livery/8-thing: PR #9 merged, but it holds 1 commit(s) past the"
        " merged head; kept" in lines
    )
    assert tree.is_dir()
    # At the merged head: nothing only here, whatever the squash did
    # to the ancestry (the branch's commit is no ancestor of main).
    _git(tree, "reset", "-q", "--hard", tip)
    lines = _sweep.sweep_worktrees(home, dry_run=False, unattended=False)
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
    lines = _sweep.sweep_worktrees(home, dry_run=False, unattended=False)
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
    lines = _sweep.sweep_worktrees(home, dry_run=False, unattended=False)
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
    # Unattended never asks the forge.
    assert _sweep.sweep_branches(checkout, dry_run=True, unattended=True) == []
    lines = _sweep.sweep_branches(checkout, dry_run=True, unattended=False)
    assert "branch docs/one: PR #9 merged, nothing only here; would remove" in lines
    assert (
        "branch docs/two: PR #9 merged, but it holds 1 commit(s) past the merged"
        " head; kept" in lines
    )
    assert "branch feat/3-open: no merged pull request; kept" in lines
    assert "docs/one" in _git(checkout, "branch", "--list", "docs/one")  # a dry run
    # Standing on the merged branch: named for sync, never moved.
    _git(checkout, "switch", "-q", "docs/one")
    lines = _sweep.sweep_branches(checkout, dry_run=False, unattended=False)
    assert (
        "branch docs/one: PR #9 merged, but the checkout stands on it;"
        " `fm sync` steps off it" in lines
    )
    _git(checkout, "switch", "-q", "main")
    lines = _sweep.sweep_branches(checkout, dry_run=False, unattended=False)
    assert "branch docs/one: PR #9 merged, nothing only here; removed" in lines
    assert _git(checkout, "branch", "--list", "docs/one") == ""
    assert "docs/two" in _git(checkout, "branch", "--list", "docs/two")
    # The forge down: every branch stays, and the reason is the forge's.
    monkeypatch.setattr(_sweep, "_repository", lambda _root: _forge(broken=True))

    lines = _sweep.sweep_branches(checkout, dry_run=False, unattended=False)
    assert lines and all(
        "the forge could not be asked" in line and line.endswith("kept")
        for line in lines
    )
