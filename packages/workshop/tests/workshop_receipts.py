"""A checkout whose receipts are current, for the tests of the sync and its hook.

`synced` builds the four receipts a sync leaves: the uv lock and the
manifests the venv was installed from, a tool lock with its receipt
and the stubs beside it, and the delivery's own receipt. Each piece
has its own builder, so a test can leave one out or move one.
"""

from __future__ import annotations

import json
import platform
from pathlib import Path

from livery.toolroom.store import LOCK_FILE, Lock, Locked, host_key
from livery.workshop import _reconcile
from livery.workshop._fragment_engine import LOCAL_RECEIPT
from livery.workshop._tools import TYPINGS, Receipt, receipts_dir

LOCK = "version = 1\n"


def venv(root: Path, *dists: str) -> Path:
    """A venv under *root* holding *dists*; its site-packages directory."""
    site = root / ".venv" / "lib" / "python3.12" / "site-packages"
    site.mkdir(parents=True, exist_ok=True)
    for dist in dists:
        (site / f"{dist}.dist-info").mkdir(exist_ok=True)
    return site


def synced(root: Path, *dists: str) -> None:
    """A checkout whose four receipts are current."""
    (root / "uv.lock").write_text(LOCK)
    (root / "pyproject.toml").write_text('[project]\nname = "ws"\n')
    venv(root, *dists)
    _reconcile.record_receipt(root)
    tool_lock(root, "ruff", "1.0")
    tool_receipt(root, "ruff", "1.0")
    (root / TYPINGS).mkdir(exist_ok=True)
    delivered(root)


def tool_lock(root: Path, name: str, version: str) -> None:
    """A tool lock holding *name* at *version* for this host."""
    host = host_key(platform.system(), platform.machine())
    lock = Lock(hosts=(host,), tools={name: Locked(version=version)})
    (root / LOCK_FILE).write_text(json.dumps(lock.to_json()))


def tool_receipt(root: Path, name: str, version: str, *, tool_dir: str = "") -> None:
    """A receipt saying *name* is installed at *version*, in *tool_dir* when given."""
    host = host_key(platform.system(), platform.machine())
    receipts_dir(root).mkdir(parents=True, exist_ok=True)
    receipt = Receipt(
        tool=name,
        version=version,
        host=host,
        kind="download",
        mode="path",
        tool_dir=tool_dir,
    )
    (receipts_dir(root) / f"{name}.json").write_text(json.dumps(receipt.to_json()))


def delivered(root: Path) -> None:
    """The receipt a delivery with nothing of its own leaves."""
    receipt = root / LOCAL_RECEIPT
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text("{}\n")
