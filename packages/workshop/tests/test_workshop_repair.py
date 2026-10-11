"""A checkout that is not current is synced before its command runs.

The hook's tests: the failures and the refusals first, then the sync it
runs, its mode, and what it leaves alone.
"""

from __future__ import annotations

import json
import os
import platform
from pathlib import Path

import pytest

from livery.toolroom.store import LOCK_FILE, Lock, Locked, host_key
from livery.workshop import _reconcile, _sync
from livery.workshop._fragment_engine import LOCAL_RECEIPT, apply_untracked
from livery.workshop._tools import Receipt, receipts_dir

LOCK = "version = 1\n"


def _venv(root: Path, *dists: str) -> Path:
    site = root / ".venv" / "lib" / "python3.12" / "site-packages"
    site.mkdir(parents=True, exist_ok=True)
    for dist in dists:
        (site / f"{dist}.dist-info").mkdir(exist_ok=True)
    return site


def _synced(root: Path, *dists: str) -> None:
    """A checkout whose four receipts are current."""
    (root / "uv.lock").write_text(LOCK)
    (root / "pyproject.toml").write_text('[project]\nname = "ws"\n')
    _venv(root, *dists)
    _reconcile.record_receipt(root)
    _tool_lock(root, "ruff", "1.0")
    _tool_receipt(root, "ruff", "1.0")
    _delivered(root)


def _tool_lock(root: Path, name: str, version: str) -> None:
    host = host_key(platform.system(), platform.machine())
    lock = Lock(hosts=(host,), tools={name: Locked(version=version)})
    (root / LOCK_FILE).write_text(json.dumps(lock.to_json()))


def _tool_receipt(root: Path, name: str, version: str) -> None:
    host = host_key(platform.system(), platform.machine())
    receipts_dir(root).mkdir(parents=True, exist_ok=True)
    receipt = Receipt(
        tool=name, version=version, host=host, kind="download", mode="path"
    )
    (receipts_dir(root) / f"{name}.json").write_text(json.dumps(receipt.to_json()))


def _delivered(root: Path) -> None:
    receipt = root / LOCAL_RECEIPT
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text("{}\n")


def _exploding_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    def sync(**kwargs: object) -> None:
        raise AssertionError(f"sync must not run here: {kwargs}")

    monkeypatch.setattr(_sync, "sync", sync)


# The failures first: a sync that cannot complete never stops a command.


def test_a_failed_sync_is_said_and_the_command_goes_on(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _synced(tmp_path, "something-1.0.0")
    (tmp_path / "uv.lock").write_text(LOCK + "\n[[package]]\n")

    def broken(**kwargs: object) -> None:
        raise OSError("no space left on device")

    monkeypatch.setattr(_sync, "sync", broken)
    monkeypatch.setattr(
        _reconcile, "_reexec", lambda root, cause: pytest.fail("re-ran after a failure")
    )
    env: dict[str, str] = {}
    assert _reconcile.apply(tmp_path, "check", env) is False
    err = capsys.readouterr().err
    assert "uv.lock moved past the environment" in err
    assert "did not complete (no space left on device)" in err
    assert env == {}


def test_unreadable_receipts_are_said_and_the_command_goes_on(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _exploding_sync(monkeypatch)
    monkeypatch.setattr(
        _reconcile, "current", lambda root: (_ for _ in ()).throw(OSError("locked"))
    )
    assert _reconcile.apply(tmp_path, "check", {}) is False
    assert "receipts could not be read (locked)" in capsys.readouterr().err


def test_a_process_the_hooks_sync_starts_syncs_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A delivery lists the workspace's tasks through the runner; that
    # child's own hook finds the receipts still missing, and would sync
    # again, and so on without end.
    _synced(tmp_path, "something-1.0.0")
    (tmp_path / "uv.lock").write_text(LOCK + "\n[[package]]\n")
    children: list[bool] = []
    env: dict[str, str] = {}

    def sync(**kwargs: object) -> None:
        # The child inherits the task's environment, the marker in it.
        children.append(_reconcile.apply(tmp_path, "check", env))

    monkeypatch.setattr(_sync, "sync", sync)
    monkeypatch.delenv("WORKSHOP_SYNCING", raising=False)
    assert _reconcile.apply(tmp_path, "check", env) is True
    assert children == [False]
    # The command that follows runs in the environment it started with,
    # and the process's own is never written.
    assert env == {} and "WORKSHOP_SYNCING" not in os.environ
    monkeypatch.setenv("WORKSHOP_SYNCING", "1")
    assert _reconcile.apply(tmp_path, "check", {}) is False


def test_the_sync_verb_itself_syncs_nothing_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _synced(tmp_path, "something-1.0.0")
    (tmp_path / "uv.lock").write_text(LOCK + "\n[[package]]\n")
    _exploding_sync(monkeypatch)
    # The sync verb does this work itself.
    assert _reconcile.apply(tmp_path, "sync", {}) is False


# The sync it runs: frozen, offline, on stderr, re-running on changed code.


def test_a_checkout_that_is_not_current_is_synced_frozen_and_offline_on_stderr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "uv.lock").write_text(LOCK)
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "ws"\n')
    _venv(tmp_path, "something-1.0.0")
    _tool_lock(tmp_path, "ruff", "1.0")
    calls: list[dict[str, object]] = []

    def sync(**kwargs: object) -> None:
        calls.append(kwargs)
        print("  tools: up to date")
        _reconcile.record_receipt(tmp_path)
        _tool_receipt(tmp_path, "ruff", "1.0")
        _delivered(tmp_path)

    monkeypatch.setattr(_sync, "sync", sync)
    monkeypatch.setattr(
        _reconcile,
        "_reexec",
        lambda root, cause: pytest.fail("re-ran on unchanged code"),
    )
    assert _reconcile.apply(tmp_path, "check", {}) is True
    assert calls == [{"frozen": True, "offline": True}]
    captured = capsys.readouterr()
    # A command whose stdout a shell evaluates stays clean.
    assert captured.out == ""
    assert "uv.lock moved past the environment; syncing from what" in captured.err
    assert "tools: up to date" in captured.err
    # Current now: the next command reads the receipts and nothing else.
    _exploding_sync(monkeypatch)
    assert _reconcile.current(tmp_path) == ""
    assert _reconcile.apply(tmp_path, "check", {}) is False


def test_a_sync_that_changed_installed_code_re_runs_the_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _synced(tmp_path, "something-1.0.0")
    (tmp_path / "uv.lock").write_text(LOCK + "\n[[package]]\n")

    def sync(**kwargs: object) -> None:
        site = _venv(tmp_path)
        (site / "something-1.0.0.dist-info").rename(site / "something-2.0.0.dist-info")
        _reconcile.record_receipt(tmp_path)

    monkeypatch.setattr(_sync, "sync", sync)
    ran: list[tuple[Path, str]] = []
    monkeypatch.setattr(
        _reconcile, "_reexec", lambda root, cause: ran.append((root, cause))
    )
    assert _reconcile.apply(tmp_path, "check", {}) is True
    assert ran == [(tmp_path, _reconcile.SYNCED)]


# What is current, and what is not, in the order the hook reads it.


def test_a_current_checkout_is_left_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _synced(tmp_path, "something-1.0.0")
    _exploding_sync(monkeypatch)
    assert _reconcile.current(tmp_path) == ""
    assert _reconcile.apply(tmp_path, "check", {}) is False


def test_cold_states_are_current(tmp_path: Path) -> None:
    # No lock, or a lock without a venv: footman's uv handoff owns those.
    assert _reconcile.current(tmp_path) == ""
    (tmp_path / "uv.lock").write_text(LOCK)
    assert _reconcile.current(tmp_path) == ""


def test_current_names_the_first_receipt_that_is_off(tmp_path: Path) -> None:
    _synced(tmp_path, "something-1.0.0")
    (tmp_path / "uv.lock").write_text(LOCK + "\n[[package]]\n")
    assert _reconcile.current(tmp_path) == "uv.lock moved past the environment"
    _reconcile.record_receipt(tmp_path)
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "moved"\n')
    moved = "a package manifest moved past the environment"
    assert _reconcile.current(tmp_path) == moved
    _reconcile.record_receipt(tmp_path)
    _tool_lock(tmp_path, "ruff", "2.0")
    assert _reconcile.current(tmp_path) == "ruff is at 1.0, the lock holds 2.0"
    (receipts_dir(tmp_path) / "ruff.json").unlink()
    assert _reconcile.current(tmp_path) == "no receipt for ruff"
    _tool_receipt(tmp_path, "ruff", "2.0")
    (tmp_path / LOCAL_RECEIPT).unlink()
    never = "this checkout's own files were never delivered"
    assert _reconcile.current(tmp_path) == never
    _delivered(tmp_path)
    assert _reconcile.current(tmp_path) == ""


def test_a_missing_manifests_receipt_adopts_the_manifests_on_disk(
    tmp_path: Path,
) -> None:
    # The emitted setup.sh's fresh venv writes the lock receipt alone:
    # the manifests installed are the ones on disk, adopted, and the
    # next move is seen.
    _synced(tmp_path, "something-1.0.0")
    manifests = _reconcile.manifests_receipt_path(tmp_path)
    manifests.unlink()
    assert _reconcile.current(tmp_path) == ""
    assert manifests.read_text("utf-8") == _reconcile.manifests_digest(tmp_path)


def test_an_unreadable_tool_lock_or_receipt_is_the_syncs_to_name(
    tmp_path: Path,
) -> None:
    _synced(tmp_path, "something-1.0.0")
    (receipts_dir(tmp_path) / "ruff.json").write_text("{not json")
    unread = f"{LOCK_FILE} or a receipt could not be read"
    assert _reconcile.current(tmp_path) == unread


def test_a_workspace_without_a_tool_lock_needs_no_tool_receipt(tmp_path: Path) -> None:
    _synced(tmp_path, "something-1.0.0")
    (tmp_path / LOCK_FILE).unlink()
    assert _reconcile.current(tmp_path) == ""


def test_a_delivery_with_nothing_of_its_own_still_leaves_its_receipt(
    tmp_path: Path,
) -> None:
    # Otherwise a workspace with no local files would read as never
    # delivered, and be synced before every command.
    apply_untracked(tmp_path, [])
    assert (tmp_path / LOCAL_RECEIPT).read_text() == "{}\n"
