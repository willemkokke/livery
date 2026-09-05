"""The per-command reconcile: the venv follows the lock on every run.

Runs at footman's pre-tasks moment, from the cascade hook. It
compares ``uv.lock`` against the sync receipt the last sync recorded,
syncs through uv on drift, and re-runs the command when the sync
changed installed code, so a pull that moved the lock never judges
from stale code. footman's own uv handoff already re-execs an
invocation from outside the venv; this closes the remaining gap, the
process already inside a venv that the lock has moved past.

Never fatal: on any failure the command proceeds and the failure is
reported in uv's own words. A task that genuinely cannot run without
what is missing is still caught by its own gate.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_GUARD = "WORKSHOP_RECONCILE_REEXEC"

#: The receipt's name under ``.venv``: a byte copy of ``uv.lock`` as
#: the venv last saw it. The emitted ``setup.sh`` writes the same
#: file after its own sync, so the two mechanisms share one record.
RECEIPT_NAME = ".workshop-sync-receipt"


def receipt_path(root: Path) -> Path:
    """Where *root*'s sync receipt lives."""
    return root / ".venv" / RECEIPT_NAME


def record_receipt(root: Path) -> None:
    """Record ``uv.lock`` as the venv's sync receipt; silent otherwise.

    Called after every successful sync so the next command's compare
    is a no-op instead of a second sync.
    """
    lock = root / "uv.lock"
    if lock.is_file() and (root / ".venv").is_dir():
        receipt_path(root).write_bytes(lock.read_bytes())


def is_cli_process() -> bool:
    """Whether this process is a real runner invocation.

    The reconcile is opt-in from the runner's own console script,
    never opt-out by recognising footman's manifest-refresh child:
    that child is spawned as ``python -c ...`` and a test suite runs
    under pytest, so neither carries the runner's name in ``argv[0]``
    and both leave the reconcile inert. The refresh child's contract
    is quick and quiet (no network, no prompts), and a test that
    shelled out to ``uv sync`` once per test would be slow and
    destructive.
    """
    import footman

    if not sys.argv or not sys.argv[0]:
        return False
    return Path(sys.argv[0]).stem.lower() == footman.prog()


@dataclass
class Reconciled:
    """What the reconcile decided: the testable surface.

    The reporting layer only prints from this, so a test asserts the
    decisions without string-matching English.
    """

    ran: bool = False
    """False when the workspace has no lock or no venv and nothing
    was tried; footman's uv handoff owns those cold states."""
    drifted: bool = False
    """True when the receipt disagreed with the lock."""
    synced: bool = False
    """True when the drift sync succeeded and the receipt was
    rewritten."""
    changed: tuple[str, ...] = ()
    """The ``dist-info`` names the sync added, removed, or replaced;
    non-empty means this process may be running stale code."""
    failure: str = ""
    """uv's own words when the sync failed; empty otherwise."""


def installed_distributions(root: Path) -> frozenset[str]:
    """Every ``*.dist-info`` name in the venv, across platform layouts."""
    found: set[str] = set()
    for pattern in ("lib/python*/site-packages", "Lib/site-packages"):
        for site in (root / ".venv").glob(pattern):
            found.update(entry.name for entry in site.glob("*.dist-info"))
    return frozenset(found)


def reconcile(root: Path) -> Reconciled:
    """Bring the venv up to date with the lock; what was decided.

    ``--frozen`` deliberately: ``--locked`` fails when the lock
    disagrees with ``pyproject.toml``, which would break every
    command for anyone mid-edit on a dependency. Installing the lock
    as-is is right here; the authoritative ``--locked`` assertion
    stays in CI's entry step.
    """
    lock = root / "uv.lock"
    if not lock.is_file() or not (root / ".venv").is_dir():
        return Reconciled()
    result = Reconciled(ran=True)
    lock_bytes = lock.read_bytes()
    receipt = receipt_path(root)
    if receipt.is_file() and receipt.read_bytes() == lock_bytes:
        return result
    result.drifted = True
    before = installed_distributions(root)
    import toolroom

    sync = toolroom.uv.opts(cwd=root, nofail=True, recorded=False)("sync", "--frozen")
    if sync.code != 0:
        result.failure = (
            f"uv sync --frozen exited {sync.code}:\n{sync.stdout}{sync.stderr}"
        )
        return result
    receipt.write_bytes(lock_bytes)
    result.synced = True
    after = installed_distributions(root)
    result.changed = tuple(sorted(before ^ after))
    return result


def _say(message: str) -> None:
    """One line to stderr, in ASCII, and never a failure of its own.

    The reconcile speaks on any command, on every platform, and its
    output is read back by machines that decode UTF-8. A Windows
    console encodes to the ANSI codepage, where a non-ASCII byte
    breaks that reader. Forced rather than trusted, and the write is
    guarded: a hook that fails while formatting its own progress note
    would take the command with it.
    """
    import contextlib

    with contextlib.suppress(Exception):
        sys.stderr.write(message.encode("ascii", "replace").decode("ascii") + "\n")


def apply(root: Path) -> None:
    """Reconcile, report, and re-run the command on changed code."""
    import footman

    result = reconcile(root)
    if result.failure:
        _say(f"{footman.prog()}: environment reconcile incomplete: {result.failure}")
        return
    if not result.synced:
        return
    _say(f"{footman.prog()}: environment synced from uv.lock")
    if result.changed:
        _reexec(root)


def _reexec(root: Path) -> None:
    """Re-run this command through uv on the code the sync installed.

    The sync replaced packages underneath a process already running
    them; modules imported before it are the old version, ones
    imported lazily afterwards the new, and a mixed process fails in
    ways that look like nothing in particular. Re-running costs one
    process start on a path that only happens when the lock moved.

    Guarded by an environment marker: a sync that never converges
    degrades to a note, not a spin. Launch failures degrade the same
    way; killing the command because the restart could not start
    would turn a repair into an outage. Windows has no real exec, so
    it waits and forwards the exit code.
    """
    import footman

    prog = footman.prog()
    if os.environ.get(_GUARD):
        _say(
            f"{prog}: the environment changed again after re-running;"
            " continuing on the loaded code"
        )
        return
    uv = shutil.which("uv")
    if uv is None:
        _say(f"{prog}: uv is not on PATH; continuing on the loaded code")
        return
    cmd = [uv, "run", "--project", str(root), "--no-sync", prog, *sys.argv[1:]]
    os.environ[_GUARD] = "1"
    try:
        if sys.platform == "win32":
            completed = subprocess.run(cmd, check=False)
            # The successful handoff: SystemExit derives from
            # BaseException, so the hook's Exception guard cannot
            # swallow it.
            raise SystemExit(completed.returncode)
        os.execv(uv, cmd)
    except (OSError, ValueError) as error:
        os.environ.pop(_GUARD, None)
        _say(
            f"{prog}: could not re-run on the updated code ({error});"
            " continuing on the loaded code"
        )
        return
