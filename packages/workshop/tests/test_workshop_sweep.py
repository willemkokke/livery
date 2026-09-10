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
    broken: bool = False,
) -> Any:
    class _Issues:
        def get(self, number: int) -> Any:
            if broken:
                raise RuntimeError("the forge is down")
            return SimpleNamespace(state="closed" if number in closed else "open")

    class _Pulls:
        def find_by_head(self, branch: str, *, state: str = "open") -> Any:
            if branch in merged:
                return SimpleNamespace(number=9, merged=True)
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
    checkout: Path, tmp_path: Path
) -> None:
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
