"""The sync runs before a command, and the receipts decide what it does.

The hook's tests: the failures and the refusals first, inside a run and
then alone, then the sync it runs and its mode; then the receipts the
sync reads, in the order it reads them.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from livery.toolroom.store import LOCK_FILE
from livery.workshop import _reconcile, _sync
from livery.workshop._fragment_engine import LOCAL_RECEIPT, apply_untracked
from livery.workshop._tools import TYPINGS, receipts_dir
from workshop_receipts import LOCK, delivered, synced, tool_lock, tool_receipt, venv


def _exploding_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    def sync_checkout(root: Path, **kwargs: object) -> bool:
        raise AssertionError(f"sync must not run here: {root} {kwargs}")

    monkeypatch.setattr(_sync, "sync_checkout", sync_checkout)


def _no_rerun(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        _reconcile, "_reexec", lambda root, cause: pytest.fail("re-ran the command")
    )


@pytest.fixture(autouse=True)
def _outside_the_hooks_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test outside the hook's sync, whatever launched the suite.

    The hook reads the marker from the process environment too, and a
    developer's shell can carry it: with it set, the hook runs no sync,
    and these tests would judge a sync that never ran.
    """
    monkeypatch.delenv("WORKSHOP_SYNCING", raising=False)


# The failures first: a sync that cannot complete never stops a command.


def _run_from_elsewhere(tmp_path: Path) -> tuple[Path, Path]:
    """A workspace, and a tasks file outside it whose one task runs inside it.

    The shape of a person's own tasks file that mounts the workshop: a
    task the file mounts runs in the file's directory unless it names
    another, so a task the hook called would run outside the workspace
    the command's task runs in. Returns the workspace's root and the
    tasks file.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "workshop.toml").write_text("[workspace]\nextensions = []\n")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    tasks = elsewhere / "tasks.py"
    tasks.write_text(
        textwrap.dedent(
            f"""
            import livery.footman as footman
            from livery.workshop._env_tasks import sync_first

            @footman.pre_task
            def first(inv, task):
                sync_first(inv, task)

            @footman.task(cwd={str(workspace)!r})
            def build():
                print("built")
            """
        )
    )
    return workspace.resolve(), tasks


def _in_a_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """Arm the hook as a runner invocation arms it, once for this test."""
    from livery.workshop import _env_tasks

    monkeypatch.setattr(_reconcile, "is_cli_process", lambda: True)
    monkeypatch.setattr(_env_tasks, "_SYNCED", [])
    _no_rerun(monkeypatch)


def test_a_failed_sync_leaves_the_commands_verdict_its_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The sync is not a task of the run: its failure is said on stderr,
    # no row of the run is the sync's, and the command exits 0.
    from livery.footman import fail
    from livery.footman.testing import Runner

    workspace, tasks = _run_from_elsewhere(tmp_path)
    synced_roots: list[Path] = []

    def sync_checkout(root: Path, **kwargs: object) -> bool:
        synced_roots.append(root)
        fail("the store holds no ruff")

    monkeypatch.setattr(_sync, "sync_checkout", sync_checkout)
    _in_a_run(monkeypatch)
    result = Runner().invoke("build", tasks=tasks, cwd=tasks.parent)
    assert result.exit_code == 0, result.stderr
    assert [row.task for row in result.results] == ["build"]
    assert synced_roots == [workspace]
    assert "the sync did not complete (the store holds no ruff)" in result.stderr


def test_the_hook_syncs_the_workspace_its_task_runs_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The task runs in the workspace and its tasks file lives outside
    # it: the sync runs on the root the hook found, frozen and offline,
    # never on a directory a task of its own would resolve.
    from livery.footman.testing import Runner

    workspace, tasks = _run_from_elsewhere(tmp_path)
    calls: list[tuple[Path, dict[str, object]]] = []

    def sync_checkout(root: Path, **kwargs: object) -> bool:
        calls.append((root, kwargs))
        return False

    monkeypatch.setattr(_sync, "sync_checkout", sync_checkout)
    _in_a_run(monkeypatch)
    result = Runner().invoke("build", tasks=tasks, cwd=tasks.parent)
    assert result.exit_code == 0, result.stderr
    assert calls == [(workspace, {"frozen": True, "offline": True})]
    assert "built" in result.stdout


def test_a_failed_sync_is_said_and_the_command_goes_on(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    synced(tmp_path, "something-1.0.0")

    def broken(root: Path, **kwargs: object) -> bool:
        raise OSError("no space left on device")

    monkeypatch.setattr(_sync, "sync_checkout", broken)
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

    def sync_checkout(root: Path, **kwargs: object) -> bool:
        # The child inherits the task's environment, the marker in it.
        children.append(_reconcile.apply(root, "check", env))
        return True

    monkeypatch.setattr(_sync, "sync_checkout", sync_checkout)
    _no_rerun(monkeypatch)
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


def test_the_hook_syncs_its_root_frozen_and_offline_on_stderr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    synced(tmp_path, "something-1.0.0")
    calls: list[tuple[Path, dict[str, object]]] = []

    def sync_checkout(root: Path, **kwargs: object) -> bool:
        calls.append((root, kwargs))
        print("  uv.lock moved past the environment; syncing from what is held")
        print("  tools: up to date")
        return True

    monkeypatch.setattr(_sync, "sync_checkout", sync_checkout)
    _no_rerun(monkeypatch)
    assert _reconcile.apply(tmp_path, "check", {}) is True
    assert calls == [(tmp_path, {"frozen": True, "offline": True})]
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
    monkeypatch.setattr(_sync, "sync_checkout", lambda root, **kwargs: False)
    _no_rerun(monkeypatch)
    assert _reconcile.apply(tmp_path, "check", {}) is False
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


def test_a_sync_that_changed_installed_code_re_runs_the_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    synced(tmp_path, "something-1.0.0")

    def sync_checkout(root: Path, **kwargs: object) -> bool:
        site = venv(root)
        (site / "something-1.0.0.dist-info").rename(site / "something-2.0.0.dist-info")
        _reconcile.record_receipt(root)
        return True

    monkeypatch.setattr(_sync, "sync_checkout", sync_checkout)
    ran: list[tuple[Path, str]] = []
    monkeypatch.setattr(
        _reconcile, "_reexec", lambda root, cause: ran.append((root, cause))
    )
    assert _reconcile.apply(tmp_path, "check", {}) is True
    assert ran == [(tmp_path, _reconcile.SYNCED)]


@pytest.mark.parametrize("platform", ["linux", "win32"])
def test_a_command_the_hooks_sync_re_runs_starts_without_the_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: str
) -> None:
    # The sync installs a newly listed extension and hands the command
    # to a fresh process. That process runs the command again; it is not
    # a child of the sync, so its own hook syncs and its children start
    # without the marker. The exec arm's stand-in ends the process as an
    # exec does; the Windows arm waits and exits with the code.
    synced(tmp_path, "something-1.0.0")
    monkeypatch.delenv("WORKSHOP_RECONCILE_REEXEC", raising=False)
    monkeypatch.setattr(shutil, "which", lambda name: "/stub/uv")
    monkeypatch.setattr(sys, "platform", platform)
    marked: list[str | None] = []
    handed_on: list[dict[str, str]] = []

    def execed(path: str, argv: list[str], env: dict[str, str]) -> None:
        handed_on.append(dict(env))
        raise SystemExit(0)

    def waited(
        cmd: list[str], *, check: bool, env: dict[str, str]
    ) -> subprocess.CompletedProcess[bytes]:
        handed_on.append(dict(env))
        return subprocess.CompletedProcess(cmd, 0)

    def sync_checkout(root: Path, **kwargs: object) -> bool:
        marked.append(os.environ.get("WORKSHOP_SYNCING"))
        _reconcile._reexec(root, _reconcile.INSTALLED)
        return True

    monkeypatch.setattr(os, "execve", execed)
    monkeypatch.setattr(subprocess, "run", waited)
    monkeypatch.setattr(_sync, "sync_checkout", sync_checkout)
    # Inside a task, os.environ reads the task's own environment: the
    # process environment stands for the task's here.
    with pytest.raises(SystemExit):
        _reconcile.apply(tmp_path, "check", os.environ)
    assert marked == ["1"]
    (env,) = handed_on
    assert env["WORKSHOP_RECONCILE_REEXEC"] == _reconcile.INSTALLED
    assert "WORKSHOP_SYNCING" not in env
    assert "WORKSHOP_SYNCING" not in os.environ


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
