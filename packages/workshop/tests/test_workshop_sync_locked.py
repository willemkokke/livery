"""`fm sync --locked` changes nothing a commit holds, and the push checks the locks."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from livery.footman.api import Failed
from livery.workshop._fragment_engine import Output, apply_untracked, tracked_local
from livery.workshop._sync import stale_locks

_FAILURES = (SystemExit, Failed)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=True)


def _repository(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    root.mkdir()
    _git(root, "init", "-q", "--initial-branch=main")
    _git(root, "config", "user.email", "t@acme.test")
    _git(root, "config", "user.name", "T")
    (root / "local").mkdir()
    (root / "local" / "tracked.txt").write_text("committed\n")
    (root / "committed.txt").write_text("committed\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "seed")
    return root


OUTPUTS = (
    Output("local/tracked.txt", b"rendered\n", (), local=True),
    Output("local/mine.txt", b"rendered\n", (), local=True),
    Output("committed.txt", b"rendered\n", ()),
)


def test_a_locked_write_leaves_every_tracked_file_as_it_is(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    apply_untracked(root, OUTPUTS)
    assert (root / "local" / "tracked.txt").read_text() == "committed\n"
    assert (root / "committed.txt").read_text() == "committed\n"
    assert (root / "local" / "mine.txt").read_text() == "rendered\n"


def test_a_local_output_git_tracks_is_named(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    assert tracked_local(root, OUTPUTS) == ["local/tracked.txt"]
    assert tracked_local(root, OUTPUTS[2:]) == []


def test_a_workspace_without_locks_has_none_to_judge(tmp_path: Path) -> None:
    assert stale_locks(tmp_path) == []


def test_a_lock_that_is_not_current_is_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "uv.lock").write_text("version = 1\n")
    (tmp_path / "tools.lock").write_text("{}\n")
    refused = SimpleNamespace(
        code=1,
        stdout="",
        stderr=(
            "error: The lockfile at `uv.lock` needs to be updated, but"
            " `--check` was provided.\n"
        ),
    )
    monkeypatch.setattr(
        "livery.toolroom.tools.api.uv",
        SimpleNamespace(opts=lambda **kwargs: lambda *args: refused),
    )
    monkeypatch.setattr(
        "livery.workshop._tools.lock_is_current",
        lambda root, offline=False: (False, "pytest's entry would move"),
    )
    assert stale_locks(tmp_path) == [
        "uv.lock is not current: the declarations moved past it",
        "tools.lock is not current (pytest's entry would move)",
    ]


def test_a_lock_uv_cannot_compare_is_not_called_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "uv.lock").write_text("version = 1\n")
    offline = SimpleNamespace(
        code=2, stdout="", stderr="error: Failed to fetch: `https://pypi.org/simple`\n"
    )
    monkeypatch.setattr(
        "livery.toolroom.tools.api.uv",
        SimpleNamespace(opts=lambda **kwargs: lambda *args: offline),
    )
    assert stale_locks(tmp_path) == [
        "uv.lock could not be checked (error: Failed to fetch: `https://pypi.org/simple`)"
    ]


def test_a_locked_sync_with_a_stale_lock_refuses_before_it_does_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop import _sync

    root = _repository(tmp_path)

    def refused(*args: object, **kwargs: object) -> list[str]:
        raise AssertionError("a refused sync must not reach this")

    monkeypatch.setattr(_sync, "workspace_root", lambda start=None: root)
    monkeypatch.setattr(
        _sync, "stale_locks", lambda at: ["uv.lock is not current (needs updating)"]
    )
    monkeypatch.setattr(_sync, "sweep_residue", refused)
    monkeypatch.setattr(_sync, "sync_workspace", refused)
    with pytest.raises(_FAILURES) as caught:
        _sync.sync(locked=True)
    message = str(caught.value)
    assert "uv.lock is not current" in message
    assert "sync` writes the locks; commit them" in message


@pytest.mark.parametrize("mode", ["locked", "frozen"])
def test_a_locked_or_frozen_sync_moves_no_branch_and_writes_no_tracked_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    # As uv's two modes leave its lock alone, ours leave whatever a
    # commit holds; they differ only in whether the locks are judged.
    from livery.workshop import _reconcile, _shipped_files, _sync, _templates, _uv

    root = _repository(tmp_path)
    (root / "workshop.toml").write_text("[workspace]\n")
    asked: dict[str, object] = {}

    def refused(*args: object, **kwargs: object) -> None:
        raise AssertionError("a locked sync must not reach this")

    def sync_tools(at: Path, **kwargs: object) -> None:
        asked["tools"] = kwargs

    def sync_workspace(at: Path, *, local_only: bool = False) -> list[str]:
        asked["workspace"] = local_only
        return []

    def deliver(at: Path, *, local_only: bool = False) -> list[str]:
        asked["deliver"] = local_only
        return []

    def run_uv(*args: str, root: Path) -> None:
        asked["uv"] = args

    monkeypatch.setattr(_sync, "workspace_root", lambda start=None: root)
    monkeypatch.setattr(_sync, "bring_current", refused)
    monkeypatch.setattr(_sync, "continue_on_moved_code", refused)
    monkeypatch.setattr(_sync, "fetch_store_lines", lambda at: [])
    monkeypatch.setattr(_sync, "sync_workspace", sync_workspace)
    monkeypatch.setattr("livery.workshop._tool_tasks.sync_tools", sync_tools)
    monkeypatch.setattr(_uv, "run_uv", run_uv)
    monkeypatch.setattr(_shipped_files, "deliver", deliver)
    monkeypatch.setattr(_templates, "apply_generated", refused)
    monkeypatch.setattr(_reconcile, "record_receipt", lambda at: None)
    if mode == "locked":
        _sync.sync(locked=True)
    else:
        # A stale lock is no refusal here: the locks are taken as they are.
        monkeypatch.setattr(_sync, "stale_locks", refused)
        _sync.sync(frozen=True)
    assert asked["workspace"] is True and asked["deliver"] is True
    assert asked["tools"] == {
        "frozen": mode == "frozen",
        "locked": mode == "locked",
        "offline": False,
    }
    assert f"--{mode}" in _args(asked["uv"])


def _args(value: object) -> tuple[str, ...]:
    assert isinstance(value, tuple)
    return tuple(str(item) for item in value)
