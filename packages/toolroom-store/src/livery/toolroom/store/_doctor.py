"""The store's doctor: every view the store made is whole, or mended from its tree.

Registered under footman's ``footman.doctors`` entry-point group and
loaded by ``fm doctor`` alone, so this module imports nothing of
footman: a finding is the four-attribute shape footman reads by name,
``what``, ``found``, ``fix`` and ``repair``. The doctor opens the store
under the runner's data directory and asks it for its damaged views
([livery.toolroom.store.Store.audit_views][]); each is a finding naming
the tool, the version and the paths, with
[livery.toolroom.store.Store.mend_view][] as its repair, which views the
tree the store already holds again. No lock, no catalogue, no network.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from livery.toolroom.store import Home, Store


@dataclass(frozen=True)
class Finding:
    """What footman's doctor reads: the check, what is wrong, the fix, the repair."""

    what: str
    found: str = ""
    fix: str = ""
    repair: Callable[[], None] | None = None


def doctor(**facts: Any) -> list[Finding]:
    """Audit every view under the store's home; the findings.

    Without a store under the runner's data directory there is nothing
    to audit and the one finding says so. A damaged view is a finding
    with the mend as its repair; whole views are one healthy line.
    """
    home = Home.under(Path(facts["data_dir"]))
    if not home.root.is_dir():
        return [Finding("views: no tool store here")]
    store = Store(home, offline=True)
    findings: list[Finding] = []
    for damaged in store.audit_views():
        shown = ", ".join(damaged.missing[:3])
        if len(damaged.missing) > 3:
            shown += f" and {len(damaged.missing) - 3} more"

        def mend(name: str = damaged.name, version: str = damaged.version) -> None:
            store.mend_view(name, version)

        findings.append(
            Finding(
                f"views: {damaged.name} {damaged.version}",
                f"{len(damaged.missing)} file(s) missing: {shown}",
                "fm doctor --fix views it again from the store",
                mend,
            )
        )
    if not findings:
        findings.append(Finding("views: every tool's view is whole"))
    return findings
