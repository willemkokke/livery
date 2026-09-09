"""The verified-tree record: which trees a green gate already proved.

A push to main reruns the gate on a squash that, for a branch that
sat on main's tip, is byte for byte the tree its pull request just
verified. The gate job stamps the tree id it proved green on the
`workshop/verified` series after its verdict, with the run, the
commit, and the scope its check legs ran; every check leg reads the
record first and skips the gate when a full entry names its own
tree. Tree identity, not merge shape, is the test, so the skip
reaches reverts and manual re-runs, and a squash of a stale branch
pays in full.

The record is advisory in the safe direction only: a missing entry,
an unreachable store, another tree, or a narrowed scope runs the
gate, and a stamp that cannot be written prints why. A leg leaves
its scope in a marker file beside its trace, so the stamp says
``full`` only when every check leg ran the whole gate; a pull request
narrowed by ``[ci] affected-legs`` never lets main skip.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from livery.workshop._git_ops import GitOps
from livery.workshop._state import RunContext, Series, put, read

#: The series: one file per tree id, the newest kept.
SERIES = Series("verified", window=200)

#: The record's schema; a reader ignores an entry of another version.
SCHEMA = 1

#: The marker a check leg leaves beside its trace, naming the scope
#: its gate ran, for the leg's metrics row and the gate job's stamp.
MARKER = "fm-gate.json"

#: The scope names a leg records.
FULL = "full"
AFFECTED = "affected"
NOTHING = "nothing"
VERIFIED = "verified"


@dataclass(frozen=True)
class Verified:
    """One tree's entry: the run that proved it and what the legs ran.

    Attributes:
        tree: The git tree id the gate proved.
        run: The forge's run id.
        sha: The commit the run checked out.
        scope: ``full`` when every check leg ran the whole gate.
        legs: The check legs' names, as the forge lists them.
    """

    tree: str
    run: str
    sha: str
    scope: str
    legs: tuple[str, ...]


def tree_id(git: GitOps, ref: str = "HEAD") -> str:
    """The tree id *ref* points at."""
    return git._run("rev-parse", f"{ref}^{{tree}}").strip()


def write_marker(
    root: Path, scope: str, packages: tuple[str, ...] = (), *, leg: str = ""
) -> None:
    """Leave the leg's scope, the suites it ran, and its label beside its trace.

    The metrics row, the stamp, and the coverage union read it back:
    *packages* are the suites the leg ran under a narrowed scope, and
    *leg* the label the store keys the leg's measurements by.
    """
    (root / MARKER).write_text(
        json.dumps(
            {"scope": scope, "packages": list(packages), "leg": leg}, sort_keys=True
        ),
        encoding="utf-8",
    )


def read_marker(root: Path) -> dict[str, Any]:
    """The leg's scope marker, or ``{"scope": "unknown"}`` when it left none."""
    unknown: dict[str, Any] = {"scope": "unknown", "packages": [], "leg": ""}
    path = root / MARKER
    if not path.is_file():
        return unknown
    try:
        loaded = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return unknown
    if not isinstance(loaded, dict):
        return unknown
    return {
        "scope": str(loaded.get("scope", "unknown")),
        "packages": [str(p) for p in loaded.get("packages", []) or []],
        "leg": str(loaded.get("leg", "") or ""),
    }


def record(root: Path, tree: str) -> tuple[Verified | None, str]:
    """The entry for *tree*, or ``(None, reason)``; an absent entry has no reason.

    A read the transport could not answer falls open with its words:
    the gate runs, and the line says why the record did not decide.
    """
    found = read(root, SERIES.ref)
    if found.files is None:
        return None, (
            f"the record could not be read: {found.reason}" if found.failed else ""
        )
    text = found.files.get(tree)
    if text is None:
        return None, ""
    try:
        entry = json.loads(text)
    except ValueError:
        return None, f"the entry for {tree[:12]} does not parse"
    if not isinstance(entry, dict) or entry.get("schema") != SCHEMA:
        return None, f"the entry for {tree[:12]} is of another schema"
    return (
        Verified(
            tree=tree,
            run=str(entry.get("run", "")),
            sha=str(entry.get("sha", "")),
            scope=str(entry.get("scope", "")),
            legs=tuple(str(leg) for leg in entry.get("legs", [])),
        ),
        "",
    )


def stamp(
    root: Path, run: RunContext, *, tree: str, sha: str, legs: tuple[str, ...]
) -> str:
    """Record *tree* as proved green by *run*; ``""`` or the reason.

    Only a CI run writes; the legs named must all have run the full
    gate, which the caller judged from their scopes.
    """
    entry = {
        "schema": SCHEMA,
        "tree": tree,
        "run": run.run_id,
        "sha": sha,
        "forge": run.forge,
        "scope": FULL,
        "legs": list(legs),
        "when": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    return put(
        root,
        SERIES.ref,
        {tree: json.dumps(entry, sort_keys=True)},
        message=f"verified: tree {tree[:12]} by run {run.run_id}",
        window=SERIES.window,
        ci_only=True,
    )


def stamp_from_metrics(root: Path, run: RunContext, *, sha: str) -> str:
    """Stamp the run's tree when every check leg's row says it ran the full gate.

    Reads the run's file on the metrics series, which the collect
    step put before the verdict; each check leg's row carries the
    scope its marker named. Returns the line to print.
    """
    from livery.workshop._metrics import SERIES as METRICS
    from livery.workshop._metrics import run_file

    found = read(root, METRICS.ref)
    if found.files is None:
        why = found.reason if found.failed else "no metrics series yet"
        return f"  verified: no stamp, the run's rows could not be read ({why})"
    text = found.files.get(run_file(run.run_id))
    if text is None:
        return f"  verified: no stamp, run {run.run_id} left no row"
    try:
        entry = json.loads(text)
    except ValueError:
        return f"  verified: no stamp, run {run.run_id}'s row does not parse"
    jobs = entry.get("jobs", {}) if isinstance(entry, dict) else {}
    legs = tuple(sorted(name for name in jobs if name.startswith("check")))
    if not legs:
        return "  verified: no stamp, the run had no check leg"
    narrowed = [
        name
        for name in legs
        if str((jobs[name].get("scope") or {}).get("scope", "unknown")) != FULL
    ]
    if narrowed:
        return f"  verified: no stamp, {', '.join(narrowed)} did not run the full gate"
    tree = tree_id(GitOps(root))
    why = stamp(root, run, tree=tree, sha=sha, legs=legs)
    if why:
        return f"  verified: no stamp, {why}"
    return f"  verified: tree {tree[:12]} recorded as proved green by run {run.run_id}"
