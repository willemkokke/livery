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

#: Why a command re-runs on the code on disk. The guard counts each
#: cause once per command, so a sync that moves the checkout and then
#: installs extensions re-runs twice, and none re-runs without end.
MOVED = "moved"
SYNCED = "synced"
INSTALLED = "installed"

#: Each cause as the guard's note names it.
_REASONS = {
    MOVED: "the checkout moved",
    SYNCED: "the environment synced",
    INSTALLED: "listed extensions were installed",
}

#: Set while a repair runs, so the processes it starts repair nothing. A
#: delivery lists the workspace's tasks through the runner; that child
#: would find the files still missing and deliver again, without end.
_REPAIRING = "WORKSHOP_REPAIRING"

#: The receipt's name under ``.venv``: a byte copy of ``uv.lock`` as
#: the venv last saw it. The emitted ``setup.sh`` writes the same
#: file after its own sync, so the two mechanisms share one record.
RECEIPT_NAME = ".workshop-sync-receipt"

#: The second receipt: a digest of the root manifest and every
#: member's, as the venv last installed them. The lock records
#: dependencies, not a member's entry points or its version, so a
#: HEAD move that changes a member's ``pyproject.toml`` without
#: moving the lock would leave the venv's metadata stale (a new
#: ``pytest11`` entry point the venv never learned) with the lock
#: receipt still matching.
MANIFESTS_RECEIPT_NAME = ".workshop-manifests-receipt"


def receipt_path(root: Path) -> Path:
    """Where *root*'s sync receipt lives."""
    return root / ".venv" / RECEIPT_NAME


def manifests_receipt_path(root: Path) -> Path:
    """Where *root*'s manifests receipt lives."""
    return root / ".venv" / MANIFESTS_RECEIPT_NAME


def manifests_digest(root: Path) -> str:
    """A digest of the root ``pyproject.toml`` and every package's ``pyproject.toml``.

    Path order, each path and its bytes, so a member added, removed,
    or edited changes the digest.
    """
    import hashlib

    from livery.workshop._packages import package_directories

    digest = hashlib.sha256()
    manifests = sorted(
        [
            root / "pyproject.toml",
            *(directory / "pyproject.toml" for directory in package_directories(root)),
        ]
    )
    for path in manifests:
        if path.is_file():
            digest.update(path.relative_to(root).as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()


def record_receipt(root: Path) -> None:
    """Record ``uv.lock`` and the manifests as the venv's receipts; silent otherwise.

    Called after every successful sync so the next command's compare
    is a no-op instead of a second sync.
    """
    lock = root / "uv.lock"
    if lock.is_file() and (root / ".venv").is_dir():
        receipt_path(root).write_bytes(lock.read_bytes())
        manifests_receipt_path(root).write_text(manifests_digest(root), "utf-8")


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
    import livery.footman.api as footman

    if not sys.argv or not sys.argv[0]:
        return False
    return Path(sys.argv[0]).stem.lower() == footman.prog()


@dataclass
class Reconciled:
    """What the reconcile decided: the testable surface.

    The reporting extension only prints from this, so a test asserts the
    decisions without string-matching English.
    """

    ran: bool = False
    """False when the workspace has no lock or no venv and nothing
    was tried; footman's uv handoff owns those cold states."""
    drifted: bool = False
    """True when a receipt disagreed with the lock or the manifests."""
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
    digest = manifests_digest(root)
    manifests = manifests_receipt_path(root)
    if receipt.is_file() and receipt.read_bytes() == lock_bytes:
        if not manifests.is_file():
            # A venv synced before the manifests receipt existed, or
            # the emitted setup script's fresh venv: the manifests
            # installed are the ones on disk, so they are adopted and
            # the next move is seen.
            manifests.write_text(digest, "utf-8")
            return result
        if manifests.read_text("utf-8") == digest:
            return result
    result.drifted = True
    before = installed_distributions(root)
    import livery.toolroom.tools.api as toolroom

    sync = toolroom.uv.opts(cwd=root, nofail=True, recorded=False)("sync", "--frozen")
    if sync.code != 0:
        result.failure = (
            f"uv sync --frozen exited {sync.code}:\n{sync.stdout}{sync.stderr}"
        )
        return result
    receipt.write_bytes(lock_bytes)
    manifests.write_text(digest, "utf-8")
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


def repair(root: Path) -> list[str]:
    """Write what this checkout lacks of its own; the lines that say what.

    A checkout holds two things of its own that git does not: a receipt
    per locked tool, which puts the tools on PATH, and the files the
    extensions write for it alone, whose receipt says they were
    delivered. A worktree whose start never synced holds neither, and
    its first gate finds no tool. Each is written from what the machine
    already holds, as ``sync --frozen --offline`` writes it: no network,
    and nothing a commit holds changed. A checkout holding both costs
    two file checks. A process a repair started repairs nothing.
    """
    from livery.toolroom.store.api import LOCK_FILE
    from livery.workshop._fragment_engine import LOCAL_RECEIPT
    from livery.workshop._tools import receipts_dir

    if os.environ.get(_REPAIRING):
        return []
    lines: list[str] = []
    # Set for this process's children and removed after them, so the
    # command that follows runs in the environment it started with.
    os.environ[_REPAIRING] = "1"
    try:
        if (root / LOCK_FILE).is_file() and not any(receipts_dir(root).glob("*.json")):
            from livery.workshop._sync import materialise_tools

            lines += materialise_tools(root, offline=True)
        if not (root / LOCAL_RECEIPT).is_file():
            from livery.workshop._shipped_files import deliver

            lines += deliver(root, local_only=True)
    finally:
        os.environ.pop(_REPAIRING, None)
    return lines


def apply(root: Path) -> bool:
    """Repair, reconcile, report, and re-run the command on changed code.

    Returns:
        Whether the repair wrote anything. The caller entered the
        environment before it, from receipts the repair may have written
        since, so it enters again.
    """
    import livery.footman.api as footman

    try:
        repaired = repair(root)
    except Exception as error:  # a repair never stops the command it precedes
        _say(
            f"{footman.prog()}: this checkout's own files could not be written: {error}"
        )
        repaired = []
    if repaired:
        _say(
            f"{footman.prog()}: this checkout lacked its own files (a start that"
            " never synced); wrote them from what the machine holds"
        )
    result = reconcile(root)
    if result.failure:
        _say(f"{footman.prog()}: environment reconcile incomplete: {result.failure}")
        return bool(repaired)
    if result.synced:
        _say(f"{footman.prog()}: environment synced from uv.lock")
        if result.changed:
            _reexec(root, SYNCED)
    return bool(repaired)


def _reexec(root: Path, cause: str) -> None:
    """Re-run this command through uv on the code the sync installed, for *cause*.

    The sync replaced packages underneath a process already running
    them; modules imported before it are the old version, ones
    imported lazily afterwards the new, and a mixed process fails in
    ways that look like nothing in particular. Re-running costs one
    process start on a path that only happens when the lock moved.

    Guarded by an environment marker naming the causes the command
    already re-ran for: each cause re-runs it once, so a sync that never
    converges degrades to a note, not a spin. Launch failures degrade
    the same way; killing the command because the restart could not
    start would turn a repair into an outage. Windows has no real exec,
    so it waits and forwards the exit code.

    The guard is handed to the replacement, never written into this
    process. An ambient write is a footman note, and rightly: a task
    that changes its own environment surprises its siblings. It is
    also invisible here, because the note is judged at the task's
    boundary and an exec never reaches one, so the wall that catches
    this everywhere else cannot catch it. Both spellings take the
    environment explicitly, so there is nothing to catch.
    """
    import livery.footman.api as footman

    prog = footman.prog()
    done = {part for part in os.environ.get(_GUARD, "").split(",") if part}
    if cause in done:
        _say(
            f"{prog}: this command already re-ran once because"
            f" {_REASONS[cause]}; continuing on the loaded code"
        )
        return
    uv = shutil.which("uv")
    if uv is None:
        _say(f"{prog}: uv is not on PATH; continuing on the loaded code")
        return
    cmd = [uv, "run", "--project", str(root), "--no-sync", prog, *sys.argv[1:]]
    try:
        # Whatever a plugin wants to survive the replacement rides in the
        # environment beside the guard: a profiled run's trace, and
        # whatever else subscribes later. Nothing mounted hands nothing on.
        with footman.handing_off() as handed:
            handed_on = {
                **os.environ,
                _GUARD: ",".join(sorted({*done, cause})),
                **handed,
            }
            if sys.platform == "win32":
                completed = subprocess.run(cmd, check=False, env=handed_on)
                # The successful handoff: SystemExit derives from
                # BaseException, so the hook's Exception guard cannot
                # swallow it.
                raise SystemExit(completed.returncode)
            os.execve(uv, cmd, handed_on)
    except (OSError, ValueError) as error:
        _say(
            f"{prog}: could not re-run on the updated code ({error});"
            " continuing on the loaded code"
        )
        return
