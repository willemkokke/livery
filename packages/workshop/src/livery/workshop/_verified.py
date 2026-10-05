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
its scope in a marker file beside its trace, with the tree its
narrowed gate measured from. The stamp says ``full`` when every
check leg ran the whole gate, and also when the legs ran narrowed
on top of a tree the record already names as full: every package a
narrowed leg skipped is byte for byte the base tree's, and so is
every root file the gate reads, so their verdicts carry over from
the base's run, and the row names that base. Without a full row for
the base, a narrowed run stamps nothing.

A pull request's run on GitHub tests the merge the forge made of the
pull request's head onto its base branch, a commit no later clone
holds. Its row names the two commits it merged, so the next run can
rebuild that merge and measure from it
([livery.workshop._gate_record.leg_plan][]).
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from livery.workshop._git_ops import GitError, GitOps
from livery.workshop._state import RunContext, Series

#: The series: one row per tree id, the newest kept.
SERIES = Series("verified", window=200)

#: The marker a check leg leaves beside its trace, naming the scope
#: its gate ran, for the leg's metrics row and the gate job's stamp.
MARKER = "fm-gate.json"

#: The scope names a leg records: the whole gate; the gate narrowed
#: to the affected packages; nothing, for a prose-only diff; the tree
#: already proved, nothing run; the tree already proved and the named
#: suites run measured for their lines alone, because main's coverage
#: record could not supply them at their current closure.
FULL = "full"
AFFECTED = "affected"
NOTHING = "nothing"
VERIFIED = "verified"
MEASURED = "measured"

#: The scopes a leg can leave; anything else means the leg said nothing.
KNOWN = (FULL, AFFECTED, NOTHING, VERIFIED, MEASURED)

#: The scopes of a leg whose tree the record already names: nothing
#: to stamp, whether the leg ran nothing or measured suites for
#: their lines.
PROVED = (VERIFIED, MEASURED)


@dataclass(frozen=True)
class Verified:
    """One tree's entry: the run that proved it and what the legs ran.

    Attributes:
        tree: The git tree id the gate proved.
        run: The forge's run id.
        sha: The commit the run checked out.
        scope: ``full`` when every check leg ran the whole gate, or
            when narrowed legs composed with a full base.
        legs: The check legs' names, as the forge lists them.
        base_tree: The tree a composed row rests on; empty otherwise.
        base_run: The run that proved that base; empty otherwise.
        branch: The pull request branch whose run proved the tree, the
            base of the coverage record main's run copies at the merge;
            empty for main's own run and for a row that never said.
        head_sha: On a run that tested a merge, the pull request's head
            it merged; empty otherwise.
        base_sha: On a run that tested a merge, the base branch's
            commit it merged onto; empty otherwise.
    """

    tree: str
    run: str
    sha: str
    scope: str
    legs: tuple[str, ...]
    base_tree: str = ""
    base_run: str = ""
    branch: str = ""
    head_sha: str = ""
    base_sha: str = ""


def tree_id(git: GitOps, ref: str = "HEAD") -> str:
    """The tree id *ref* points at."""
    return git._run("rev-parse", f"{ref}^{{tree}}").strip()


def write_marker(
    root: Path,
    scope: str,
    packages: tuple[str, ...] = (),
    *,
    leg: str = "",
    base_tree: str = "",
) -> None:
    """Leave the leg's scope, the suites it ran, and its label beside its trace.

    The metrics row, the stamp, and the coverage union read it back:
    *packages* are the suites the leg ran under a narrowed or a
    measured scope, *leg* the label the record keys the leg's
    measurements by, and *base_tree* the tree a narrowed gate measured
    from, which the stamp composes with.
    """
    (root / MARKER).write_text(
        json.dumps(
            {
                "scope": scope,
                "packages": list(packages),
                "leg": leg,
                "base_tree": base_tree,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def read_marker(root: Path) -> dict[str, Any]:
    """The leg's scope marker, or ``{"scope": "unknown"}`` when it left none."""
    unknown: dict[str, Any] = {
        "scope": "unknown",
        "packages": [],
        "leg": "",
        "base_tree": "",
    }
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
        "base_tree": str(loaded.get("base_tree", "") or ""),
    }


def record(root: Path, tree: str) -> tuple[Verified | None, str]:
    """The entry for *tree*, or ``(None, reason)``; an absent entry has no reason.

    A read the transport could not answer falls open with its words,
    and so does an entry that is not a row of the record's schema:
    the gate runs, and the line says why the record did not decide.
    """
    row, why = SERIES.row(root, tree)
    if row is None:
        return None, why
    return _entry(tree, row.data), ""


def _entry(tree: str, entry: dict[str, Any]) -> Verified:
    """The row of *tree* as the record's own type."""
    return Verified(
        tree=tree,
        run=str(entry.get("run", "")),
        sha=str(entry.get("sha", "")),
        scope=str(entry.get("scope", "")),
        legs=tuple(str(leg) for leg in entry.get("legs", [])),
        base_tree=str(entry.get("base_tree", "")),
        base_run=str(entry.get("base_run", "")),
        branch=str(entry.get("branch", "") or ""),
        head_sha=str(entry.get("head_sha", "") or ""),
        base_sha=str(entry.get("base_sha", "") or ""),
    )


def proved(root: Path) -> tuple[dict[str, Verified], str]:
    """Every full row, by tree, newest first, in one read; or ``({}, reason)``.

    A row of a narrowed scope proves nothing and is left out, as a
    file that is not a row of the record's schema is.
    """
    found = SERIES.rows(root)
    if found.failed:
        return {}, found.reason
    rows = (_entry(row.name, row.data) for row in found.rows)
    return {entry.tree: entry for entry in rows if entry.scope == FULL}, ""


def held(root: Path, trees: Iterable[str]) -> tuple[set[str], str]:
    """The trees among *trees* the record holds, in one read.

    ``(set(), reason)`` when the record could not be read. A file that
    is not a row of the record's schema is skipped the way every read
    skips it, so its tree reads as not held; an absent record holds
    nothing, without a reason.
    """
    found = SERIES.rows(root)
    if found.failed:
        return set(), found.reason
    names = {row.name for row in found.rows}
    return {tree for tree in trees if tree in names}, ""


def stamp(
    root: Path,
    run: RunContext,
    *,
    tree: str,
    sha: str,
    legs: tuple[str, ...],
    base: Verified | None = None,
    branch: str = "",
    merged: tuple[str, str] | None = None,
) -> str:
    """Record *tree* as proved green by *run*; ``""`` or the reason.

    Only a CI run writes. The legs named all ran the full gate, or
    ran narrowed on top of *base*, a full row the caller read from
    the record; a composed row names that base and its run. *branch*
    is the pull request branch the run came from, so main's run after
    the squash finds the branch's coverage record to copy. *merged*
    are the base branch's commit and the head a merge checkout joined,
    so the branch's next run can rebuild the merge.
    """
    entry: dict[str, object] = {
        "tree": tree,
        "run": run.run_id,
        "sha": sha,
        "forge": run.forge,
        "scope": FULL,
        "legs": list(legs),
    }
    if branch:
        entry["branch"] = branch
    if merged is not None:
        entry["base_sha"], entry["head_sha"] = merged
    basis = ""
    if base is not None:
        entry["base_tree"] = base.tree
        entry["base_run"] = base.run
        basis = f" on top of {base.tree[:12]}"
    return SERIES.put(
        root,
        {tree: entry},
        message=f"verified: tree {tree[:12]} by run {run.run_id}{basis}",
    )


def stamp_from_metrics(root: Path, run: RunContext, *, sha: str) -> str:
    """Stamp the run's tree from what its check legs' rows say they ran.

    Reads the run's file on the metrics series, which the collect
    step put before the verdict; each check leg's row carries the
    scope its marker named. Every leg full: the tree is stamped.
    Legs narrowed (``affected``, or ``nothing`` for a prose-only
    diff): the tree is stamped when every narrowed leg measured from
    one tree and the record names that tree as full, and the row
    names that base; otherwise nothing is written and the line says
    which proof is missing. On a merge checkout the row also names
    the two commits merged. Returns the line to print.
    """
    from livery.workshop._metrics import SERIES as METRICS
    from livery.workshop._metrics import run_file

    row, why = METRICS.row(root, run_file(run.run_id))
    if why:
        return f"  verified: no stamp, {why}"
    if row is None:
        return f"  verified: no stamp, run {run.run_id} left no row"
    jobs = row.data.get("jobs", {})
    if not isinstance(jobs, dict):
        jobs = {}
    legs = tuple(sorted(name for name in jobs if name.startswith("check")))
    if not legs:
        return "  verified: no stamp, the run had no check leg"
    scopes = {
        name: str((jobs[name].get("scope") or {}).get("scope", "unknown"))
        for name in legs
    }
    unknown = [name for name, scope in scopes.items() if scope not in KNOWN]
    if unknown:
        return f"  verified: no stamp, {', '.join(unknown)} left no scope"
    git = GitOps(root)
    tree = tree_id(git)
    if all(scope in PROVED for scope in scopes.values()):
        return f"  verified: tree {tree[:12]} is already recorded; nothing to stamp"
    try:
        merged = git.merge_parents()
    except GitError as error:
        return f"  verified: no stamp, HEAD's parents could not be read ({error})"
    if all(scope in (FULL, *PROVED) for scope in scopes.values()):
        why = stamp(
            root,
            run,
            tree=tree,
            sha=sha,
            legs=legs,
            branch=run.head_ref,
            merged=merged,
        )
        if why:
            return f"  verified: no stamp, {why}"
        return (
            f"  verified: tree {tree[:12]} recorded as proved green by run {run.run_id}"
        )
    # Narrowed legs prove the packages they ran; the base tree's row
    # proves the rest, since those packages and every root file the
    # gate reads are the base's bytes, or the legs would have widened.
    narrowed = {
        name: scope for name, scope in scopes.items() if scope not in (FULL, *PROVED)
    }
    bases = {
        name: str((jobs[name].get("scope") or {}).get("base_tree", "") or "")
        for name in narrowed
    }
    unnamed = sorted(name for name, base in bases.items() if not base)
    if unnamed:
        return (
            f"  verified: no stamp, {', '.join(unnamed)} named no tree its"
            " narrowed gate measured from"
        )
    if len(set(bases.values())) > 1:
        named = ", ".join(f"{name} from {base[:12]}" for name, base in bases.items())
        return f"  verified: no stamp, the legs measured from different trees ({named})"
    base = next(iter(bases.values()))
    base_row, why = record(root, base)
    if why:
        return (
            f"  verified: no stamp, the base tree {base[:12]} could not be read ({why})"
        )
    if base_row is None or base_row.scope != FULL:
        return (
            f"  verified: no stamp, {', '.join(narrowed)} ran narrowed on base tree"
            f" {base[:12]}, which the record has not proved in full"
        )
    why = stamp(
        root,
        run,
        tree=tree,
        sha=sha,
        legs=legs,
        base=base_row,
        branch=run.head_ref,
        merged=merged,
    )
    if why:
        return f"  verified: no stamp, {why}"
    return (
        f"  verified: tree {tree[:12]} recorded as proved green by run {run.run_id}"
        f" on top of tree {base[:12]} (run {base_row.run})"
    )
