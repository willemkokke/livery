"""The sync verb runs before a command, and the receipts decide what it does.

The hook's tests: the failures and the refusals first, then the sync it
runs and its mode; then the receipts the verb reads, in the order it
reads them.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from livery.toolroom.store import LOCK_FILE
from livery.workshop import _reconcile, _sync
from livery.workshop._fragment_engine import LOCAL_RECEIPT, apply_untracked
from livery.workshop._tools import TYPINGS, receipts_dir
from workshop_receipts import LOCK, delivered, synced, tool_lock, tool_receipt, venv


def _exploding_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    def sync(**kwargs: object) -> bool:
        raise AssertionError(f"sync must not run here: {kwargs}")

    monkeypatch.setattr(_sync, "sync", sync)


def _no_rerun(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        _reconcile, "_reexec", lambda root, cause: pytest.fail("re-ran the command")
    )


# The failures first: a sync that cannot complete never stops a command.


def test_a_failed_sync_is_said_and_the_command_goes_on(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    synced(tmp_path, "something-1.0.0")

    def broken(**kwargs: object) -> bool:
        raise OSError("no space left on device")

    monkeypatch.setattr(_sync, "sync", broken)
    _no_rerun(monkeypatch)
    env: dict[str, str] = {}
    assert _reconcile.apply(tmp_path, "check", env) is False
    assert "did not complete (no space left on device)" in capsys.readouterr().err
    assert env == {}


def test_a_process_the_hooks_sync_starts_syncs_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A delivery lists the workspace's tasks through the runner; that
    # child's own hook would run the sync again, and so on without end.
    synced(tmp_path, "something-1.0.0")
    children: list[bool] = []
    env: dict[str, str] = {}

    def sync(**kwargs: object) -> bool:
        # The child inherits the task's environment, the marker in it.
        children.append(_reconcile.apply(tmp_path, "check", env))
        return True

    monkeypatch.setattr(_sync, "sync", sync)
    _no_rerun(monkeypatch)
    monkeypatch.delenv("WORKSHOP_SYNCING", raising=False)
    assert _reconcile.apply(tmp_path, "check", env) is True
    assert children == [False]
    # The command that follows runs in the environment it started with,
    # and the process's own is never written.
    assert env == {} and "WORKSHOP_SYNCING" not in os.environ
    monkeypatch.setenv("WORKSHOP_SYNCING", "1")
    assert _reconcile.apply(tmp_path, "check", {}) is False


def test_the_sync_verb_itself_runs_no_sync_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    synced(tmp_path, "something-1.0.0")
    _exploding_sync(monkeypatch)
    # The sync verb is the work; nothing runs ahead of it.
    assert _reconcile.apply(tmp_path, "sync", {}) is False


# The sync it runs: frozen, offline, on stderr, re-running on changed code.


def test_the_hook_runs_the_sync_verb_frozen_and_offline_on_stderr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    synced(tmp_path, "something-1.0.0")
    calls: list[dict[str, object]] = []

    def sync(**kwargs: object) -> bool:
        calls.append(kwargs)
        print("  uv.lock moved past the environment; syncing from what is held")
        print("  tools: up to date")
        return True

    monkeypatch.setattr(_sync, "sync", sync)
    _no_rerun(monkeypatch)
    assert _reconcile.apply(tmp_path, "check", {}) is True
    assert calls == [{"frozen": True, "offline": True}]
    captured = capsys.readouterr()
    # A command whose stdout a shell evaluates stays clean.
    assert captured.out == ""
    assert "uv.lock moved past the environment; syncing from what" in captured.err
    assert "tools: up to date" in captured.err


def test_a_sync_that_did_nothing_is_silent_and_enters_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The verb read its receipts and found the checkout current: the
    # hook says nothing, and the caller does not enter the environment
    # again for nothing.
    synced(tmp_path, "something-1.0.0")
    monkeypatch.setattr(_sync, "sync", lambda **kwargs: False)
    _no_rerun(monkeypatch)
    assert _reconcile.apply(tmp_path, "check", {}) is False
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


def test_a_sync_that_changed_installed_code_re_runs_the_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    synced(tmp_path, "something-1.0.0")

    def sync(**kwargs: object) -> bool:
        site = venv(tmp_path)
        (site / "something-1.0.0.dist-info").rename(site / "something-2.0.0.dist-info")
        _reconcile.record_receipt(tmp_path)
        return True

    monkeypatch.setattr(_sync, "sync", sync)
    ran: list[tuple[Path, str]] = []
    monkeypatch.setattr(
        _reconcile, "_reexec", lambda root, cause: ran.append((root, cause))
    )
    assert _reconcile.apply(tmp_path, "check", {}) is True
    assert ran == [(tmp_path, _reconcile.SYNCED)]


# What is current, and what is not, in the order the verb reads it.


def test_a_current_checkout_reads_as_current(tmp_path: Path) -> None:
    synced(tmp_path, "something-1.0.0")
    assert _reconcile.current(tmp_path) == ""


def test_cold_states_have_nothing_to_compare_and_are_not_current(
    tmp_path: Path,
) -> None:
    assert _reconcile.current(tmp_path) == "there is no uv.lock yet"
    (tmp_path / "uv.lock").write_text(LOCK)
    assert _reconcile.current(tmp_path) == "there is no environment yet"


def test_current_names_the_first_receipt_that_is_off(tmp_path: Path) -> None:
    synced(tmp_path, "something-1.0.0")
    (tmp_path / "uv.lock").write_text(LOCK + "\n[[package]]\n")
    assert _reconcile.current(tmp_path) == "uv.lock moved past the environment"
    _reconcile.record_receipt(tmp_path)
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "moved"\n')
    moved = "a package manifest moved past the environment"
    assert _reconcile.current(tmp_path) == moved
    _reconcile.record_receipt(tmp_path)
    tool_lock(tmp_path, "ruff", "2.0")
    assert _reconcile.current(tmp_path) == "ruff is at 1.0, the lock holds 2.0"
    (receipts_dir(tmp_path) / "ruff.json").unlink()
    assert _reconcile.current(tmp_path) == "no receipt for ruff"
    gone = tmp_path / "store" / "ruff"
    tool_receipt(tmp_path, "ruff", "2.0", tool_dir=str(gone))
    assert _reconcile.current(tmp_path) == "the store no longer holds ruff"
    gone.mkdir(parents=True)
    shutil.rmtree(tmp_path / TYPINGS)
    assert _reconcile.current(tmp_path) == f"no stubs in {TYPINGS}/"
    (tmp_path / TYPINGS).mkdir()
    (tmp_path / LOCAL_RECEIPT).unlink()
    never = "this checkout's own files were never delivered"
    assert _reconcile.current(tmp_path) == never
    delivered(tmp_path)
    assert _reconcile.current(tmp_path) == ""


def test_a_missing_manifests_receipt_adopts_the_manifests_on_disk(
    tmp_path: Path,
) -> None:
    # The emitted setup.sh's fresh venv writes the lock receipt alone:
    # the manifests installed are the ones on disk, adopted, and the
    # next move is seen.
    synced(tmp_path, "something-1.0.0")
    manifests = _reconcile.manifests_receipt_path(tmp_path)
    manifests.unlink()
    assert _reconcile.current(tmp_path) == ""
    assert manifests.read_text("utf-8") == _reconcile.manifests_digest(tmp_path)


def test_an_unreadable_tool_lock_or_receipt_is_the_syncs_to_name(
    tmp_path: Path,
) -> None:
    synced(tmp_path, "something-1.0.0")
    (receipts_dir(tmp_path) / "ruff.json").write_text("{not json")
    unread = f"{LOCK_FILE} or a receipt could not be read"
    assert _reconcile.current(tmp_path) == unread


def test_a_workspace_without_a_tool_lock_needs_no_tool_receipt(tmp_path: Path) -> None:
    synced(tmp_path, "something-1.0.0")
    (tmp_path / LOCK_FILE).unlink()
    shutil.rmtree(tmp_path / TYPINGS)
    assert _reconcile.current(tmp_path) == ""


def test_a_delivery_with_nothing_of_its_own_still_leaves_its_receipt(
    tmp_path: Path,
) -> None:
    # Otherwise a workspace with no local files would read as never
    # delivered, and be synced before every command.
    apply_untracked(tmp_path, [])
    assert (tmp_path / LOCAL_RECEIPT).read_text() == "{}\n"
