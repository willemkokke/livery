"""A checkout that lacks its own files gets them before its command runs."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from livery.workshop import _reconcile
from livery.workshop._fragment_engine import LOCAL_RECEIPT, apply_untracked


def _calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    called: list[str] = []

    def materialise(root: Path, *, offline: bool = False) -> list[str]:
        called.append(f"tools offline={offline}")
        return ["  tools: 18 receipt(s), all present"]

    def deliver(root: Path, *, local_only: bool = False) -> list[str]:
        called.append(f"deliver local_only={local_only}")
        return ["  wrote .workshop/fragments/rules.md"]

    monkeypatch.setattr("livery.workshop._sync.materialise_tools", materialise)
    monkeypatch.setattr("livery.workshop._shipped_files.deliver", deliver)
    return called


def _delivered(root: Path) -> None:
    receipt = root / LOCAL_RECEIPT
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text("{}\n")


# The failure first: a repair that cannot write never stops a command.


def test_a_failed_repair_is_said_and_the_command_goes_on(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def broken(root: Path) -> list[str]:
        raise OSError("no space left on device")

    monkeypatch.setattr(_reconcile, "repair", broken)
    monkeypatch.setattr(_reconcile, "reconcile", lambda root: _reconcile.Reconciled())
    assert not _reconcile.apply(tmp_path)
    assert "could not be written: no space left on device" in capsys.readouterr().err


def test_a_process_the_repair_starts_repairs_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A delivery lists the workspace's tasks through the runner; that
    # child's own repair finds the files still missing, and would
    # deliver again, and so on without end.
    (tmp_path / "toolroom.lock").write_text("{}\n")
    called = _calls(monkeypatch)
    children: list[list[str]] = []

    def deliver(root: Path, *, local_only: bool = False) -> list[str]:
        called.append(f"deliver local_only={local_only}")
        children.append(_reconcile.repair(root))  # the child inherits the marker
        return ["  wrote .workshop/fragments/rules.md"]

    monkeypatch.setattr("livery.workshop._shipped_files.deliver", deliver)
    monkeypatch.delenv("WORKSHOP_REPAIRING", raising=False)
    assert _reconcile.repair(tmp_path)
    assert children == [[]]
    assert called == ["tools offline=True", "deliver local_only=True"]
    # The command that follows runs in the environment it started with.
    assert "WORKSHOP_REPAIRING" not in os.environ


def test_a_checkout_that_never_synced_gets_its_tools_and_its_own_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "toolroom.lock").write_text("{}\n")
    called = _calls(monkeypatch)
    assert _reconcile.repair(tmp_path)
    # From what the machine holds, and touching nothing a commit holds.
    assert called == ["tools offline=True", "deliver local_only=True"]


def test_a_checkout_holding_both_is_left_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "toolroom.lock").write_text("{}\n")
    receipts = tmp_path / ".workshop" / "receipts"
    receipts.mkdir(parents=True)
    (receipts / "ruff.json").write_text("{}\n")
    _delivered(tmp_path)
    called = _calls(monkeypatch)
    assert _reconcile.repair(tmp_path) == []
    assert called == []


def test_a_workspace_without_a_tool_lock_needs_no_tool_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _delivered(tmp_path)
    called = _calls(monkeypatch)
    assert _reconcile.repair(tmp_path) == []
    assert called == []


def test_a_delivery_with_nothing_of_its_own_still_leaves_its_receipt(
    tmp_path: Path,
) -> None:
    # Otherwise a workspace with no local files would read as never
    # delivered, and be repaired before every command.
    apply_untracked(tmp_path, [])
    assert (tmp_path / LOCAL_RECEIPT).read_text() == "{}\n"
