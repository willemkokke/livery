"""The per-command hook: a sync runs before a command, frozen and offline.

Runs at footman's per-task moment, before the first task of a command,
from the hook in [livery.workshop._env_tasks][]. It runs the ``sync``
verb's work on the root the hook found
([livery.workshop._sync.sync_checkout][]), in the mode that changes
nothing a commit holds and touches no network, as a plain call: footman
gives a task called from a hook a directory of its own and a row in the
run's report, and a failed row fails the command. The sync reads the
receipts a checkout keeps of its last sync
([livery.workshop._reconcile.current][]): the uv lock and the
manifests the venv was installed from, the tool receipts against
``toolroom.lock``, and the receipt of the files the extensions write
for the checkout alone. A current checkout costs those reads and
nothing else; one that is not is brought current from what the machine
holds, and the command re-runs on the installed code when that sync
changed it, so a pull that moved the lock never judges from stale
code. When ``sync`` is the command, the hook leaves the work to it.

The receipts live here beside the hook. What a sync records at its end
and what it reads at its start are one record.

Never fatal: on any failure the command proceeds and the failure is
reported in the sync's own words. A task that cannot run without what
is missing is still caught by its own gate.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import MutableMapping
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

#: Set while the hook's sync runs, so the processes it starts sync
#: nothing: a delivery lists the workspace's tasks through the runner,
#: and that child would find the receipts still missing and sync again,
#: without end.
_SYNCING = "WORKSHOP_SYNCING"

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
    import livery.footman as footman

    if not sys.argv or not sys.argv[0]:
        return False
    return Path(sys.argv[0]).stem.lower() == footman.prog()


def installed_distributions(root: Path) -> dict[str, str]:
    """Every ``*.dist-info`` name in the venv, to its entry points' digest.

    Read across platform layouts. A name alone misses an editable
    member re-installed at the same version with new entry points: its
    directory keeps its name, and a process that mounted the old entry
    points would go on with them. The digest of each
    ``entry_points.txt`` (empty without one) makes that re-install a
    change.
    """
    import hashlib

    found: dict[str, str] = {}
    for pattern in ("lib/python*/site-packages", "Lib/site-packages"):
        for site in (root / ".venv").glob(pattern):
            for entry in site.glob("*.dist-info"):
                try:
                    points = (entry / "entry_points.txt").read_bytes()
                except OSError:
                    points = b""
                found[entry.name] = hashlib.sha256(points).hexdigest() if points else ""
    return found


def current(root: Path) -> str:
    """Why *root* is not current, in a few words; empty when it is.

    Four receipts, all local reads. The uv lock receipt and the
    manifests receipt against ``uv.lock`` and the manifests on disk;
    a venv synced before the manifests receipt existed adopts the
    manifests on disk as the ones installed, since the emitted
    ``setup.sh``'s fresh venv is that case, and the next move is seen.
    Then a receipt per tool ``toolroom.lock`` holds for this host, at
    the locked version and still in the store, with the stubs beside
    them; then the receipt a delivery leaves. A checkout with no lock
    or no venv has nothing to compare and is not current.
    """
    lock = root / "uv.lock"
    if not lock.is_file():
        return "there is no uv.lock yet"
    if not (root / ".venv").is_dir():
        return "there is no environment yet"
    receipt = receipt_path(root)
    if not receipt.is_file() or receipt.read_bytes() != lock.read_bytes():
        return "uv.lock moved past the environment"
    digest = manifests_digest(root)
    manifests = manifests_receipt_path(root)
    if not manifests.is_file():
        manifests.write_text(digest, "utf-8")
    elif manifests.read_text("utf-8") != digest:
        return "a package manifest moved past the environment"
    why = tools_current(root)
    if why:
        return why
    from livery.workshop._fragment_engine import LOCAL_RECEIPT

    if not (root / LOCAL_RECEIPT).is_file():
        return "this checkout's own files were never delivered"
    return ""


def tools_current(root: Path) -> str:
    """Why the tool receipts do not match ``toolroom.lock``; empty when they do.

    A receipt matches when it names the locked version and the
    directory it points into is still in the store; a receipt whose
    tool the store has lost reads as off, so a sync supplies it again.
    The stubs the checkers read stand beside the receipts: a checkout
    locked for this host with no ``typings/`` is off too. A checkout
    with no ``toolroom.lock`` has nothing to match and is current here.
    """
    import platform

    from livery.toolroom.store import LOCK_FILE, Lock, LockError, host_key
    from livery.workshop._tools import TYPINGS, receipts

    if not (root / LOCK_FILE).is_file():
        return ""
    try:
        lock = Lock.load(root / LOCK_FILE)
        held = receipts(root)
    except (LockError, ValueError, OSError):
        # An unreadable lock or receipt is the sync's to name, not
        # the hook's to judge: the sync reads both and refuses by name.
        return f"{LOCK_FILE} or a receipt could not be read"
    host = host_key(platform.system(), platform.machine())
    for name in lock.on_host(host):
        receipt = held.get(name)
        if receipt is None:
            return f"no receipt for {name}"
        if receipt.version != lock.tools[name].version:
            locked = lock.tools[name].version
            return f"{name} is at {receipt.version}, the lock holds {locked}"
        if receipt.tool_dir and not Path(receipt.tool_dir).is_dir():
            return f"the store no longer holds {name}"
    if lock.on_host(host) and not (root / TYPINGS).is_dir():
        return f"no stubs in {TYPINGS}/"
    return ""


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


def apply(root: Path, task: str, env: MutableMapping[str, str]) -> bool:
    """Sync *root* before *task*, the command's first; whether the sync did work.

    Nothing happens when *task* is ``sync``, the verb itself, or inside
    a process the hook's own sync started, which *env*, the task's own
    environment, marks for the processes the sync starts. Otherwise the
    verb's work runs on *root*, frozen and offline, as a plain call
    ([livery.workshop._sync.sync_checkout][]), its lines on stderr so
    a command whose stdout a shell evaluates stays clean. It reads the
    receipts first and does nothing, silently, for a current checkout;
    otherwise it says what is off and brings the checkout current, and
    the command re-runs on the installed code when that changed it. A
    sync that fails is said and the command runs on what is here: the
    sync is not a task of the run, so its failure adds no failed row,
    and the command's verdict is its own.

    Returns:
        Whether the sync did work. The caller entered the environment
        before it, from receipts the sync may have written since, so it
        enters again.
    """
    import contextlib

    import livery.footman as footman

    if os.environ.get(_SYNCING) or env.get(_SYNCING) or task == "sync":
        return False
    prog = footman.prog()
    before = installed_distributions(root)
    # Set in the task's own environment, which the sync's children see
    # and no sibling does, and removed after them.
    env[_SYNCING] = "1"
    try:
        from livery.workshop._sync import sync_checkout

        with contextlib.redirect_stdout(sys.stderr):
            worked = sync_checkout(root, frozen=True, offline=True)
    except Exception as error:  # the hook never stops the command it precedes
        _say(f"{prog}: the sync did not complete ({error}); running on what is here")
        return False
    finally:
        env.pop(_SYNCING, None)
    if not worked:
        return False
    after = installed_distributions(root)
    if any(
        before.get(name) != after.get(name) for name in before.keys() | after.keys()
    ):
        _reexec(root, SYNCED)
    return True


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

    The hook's sync marker is never handed on. The replacement runs the
    command again; it is not a process the sync starts, so its own hook
    syncs and its children start without the marker.
    """
    import livery.footman as footman

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
            handed_on: dict[str, str] = {
                **os.environ,
                _GUARD: ",".join(sorted({*done, cause})),
                **handed,
            }
            handed_on.pop(_SYNCING, None)
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
