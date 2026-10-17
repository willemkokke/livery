"""The verified-tree record: which trees a green gate already proved, per platform.

A push to main reruns the gate on a squash that, for a branch that
sat on main's tip, is byte for byte the tree its pull request just
verified. The gate job stamps the tree id it proved green on the
`workshop/verified` series after its verdict, with the run, the
commit, the scope its check legs ran, and the legs' labels
(``check-ubuntu-latest-3.14``); every check leg reads the record
first and skips the gate when a full entry names its own tree for
its own platform leg. Tree identity, not merge shape, is the test,
so the skip reaches reverts and manual re-runs, and a squash of a
stale branch pays in full.

A row proves its tree for the legs it names and for no other
platform. After a pull request's run on one platform, the merge's
run on that platform ends its check at once, the other platforms run
the whole gate, and the stamp names every leg of the run, the
skipped one included, so the tree's row then names them all. The
machine's reflex ([livery.workshop._gate_record.plan][]) is not a
leg: it roots on any row (`held`), since a machine is its own
platform and its gate is the reflex, not the verdict.

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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from livery import git
from livery.workshop._ci_context import RunContext, state_store
from livery.workshop._shard import platform_leg
from livery.workshop._speed import forge_shard_row, leg_label, leg_of

#: The series: one row per tree id, the newest kept.
SERIES = git.Series("verified", window=200)

#: The marker a check leg leaves beside its trace, naming the scope
#: its gate ran, for the leg's metrics row and the gate job's stamp.
MARKER = "fm-gate.json"

#: The scope names a leg records: the whole gate; the gate narrowed
#: to the affected packages; nothing, for a prose-only diff; the tree
#: already proved, so nothing to stamp, whatever the entries after the
#: gate ran.
FULL = "full"
AFFECTED = "affected"
NOTHING = "nothing"
VERIFIED = "verified"

#: The scopes a leg can leave; anything else means the leg said nothing.
KNOWN = (FULL, AFFECTED, NOTHING, VERIFIED)


@dataclass(frozen=True)
class Verified:
    """One tree's entry: the run that proved it and what the legs ran.

    Attributes:
        tree: The git tree id the gate proved.
        run: The forge's run id.
        sha: The commit the run checked out.
        scope: ``full`` when every check leg ran the whole gate, or
            when narrowed legs composed with a full base.
        legs: The check legs' labels (``check-ubuntu-latest-3.14``),
            one per check leg of the run. A row written with the
            legs' display names, as the forge lists them, reads
            through [livery.workshop._speed.leg_label][].
        base_tree: The tree a composed row rests on; empty otherwise.
        base_run: The run that proved that base; empty otherwise.
        branch: The pull request branch whose run proved the tree, whose
            records main's run reads at the merge; empty for main's own
            run and for a row that never said.
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


def tree_id(root: Path, ref: str = "HEAD") -> str:
    """The tree id *ref* points at."""
    # `<ref>:` names the root tree, as `^{tree}` does without a caret,
    # which cmd.exe drops on its way to a git run through a `.cmd` launcher.
    return git.run(root, "rev-parse", f"{ref}:").strip()


def write_marker(
    root: Path,
    scope: str,
    packages: tuple[str, ...] = (),
    *,
    leg: str = "",
    base_tree: str = "",
) -> None:
    """Leave the leg's scope, the suites it ran, and its label beside its trace.

    The metrics row, the stamp, and the entries after the gate read it
    back: *packages* are the suites the leg ran under a narrowed scope,
    *leg* the label the leg's rows are keyed by, and *base_tree* the
    tree a narrowed gate measured from, which the stamp composes with.
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


@dataclass(frozen=True)
class LegScope:
    """What the gate ran on one check leg, as the marker the leg left says.

    Attributes:
        scope: What the gate ran. ``full``: the whole gate.
            ``affected``: the gate narrowed to the packages a change
            affects. ``nothing``: no package's steps, for a change that
            affects no package, such as prose alone; the workspace
            steps whose inputs it changed still run. ``verified``: no
            step, since the verified record already proves the tree.
            ``unknown``: the leg left no marker, or one that does not
            parse.
        packages: The paths of the suites a narrowed gate ran; empty
            for every other scope.
        leg: The label the leg's rows are keyed by
            (``check-ubuntu-latest-3.14``); empty when the marker names
            none.
        base_tree: The tree a narrowed gate measured from; empty for
            every other scope.
    """

    scope: str
    packages: tuple[str, ...] = ()
    leg: str = ""
    base_tree: str = ""


def leg_scope(root: Path) -> LegScope:
    """Read what the gate ran on this leg from the marker it left at *root*.

    The gate leaves the marker on a CI run's check leg, so the leg's
    entries after the gate read it there. A gate outside CI leaves
    none.

    Returns:
        The leg's scope; ``unknown`` when there is no marker, or the
        marker is not a JSON object.
    """
    unknown = LegScope("unknown")
    path = root / MARKER
    if not path.is_file():
        return unknown
    try:
        loaded = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return unknown
    if not isinstance(loaded, dict):
        return unknown
    return LegScope(
        scope=str(loaded.get("scope", "unknown")),
        packages=tuple(str(p) for p in loaded.get("packages", []) or []),
        leg=str(loaded.get("leg", "") or ""),
        base_tree=str(loaded.get("base_tree", "") or ""),
    )


@dataclass(frozen=True)
class VerifiedBranch:
    """The branch whose run proved a tree, or why the record could not say.

    Attributes:
        branch: The pull request branch whose run proved the tree;
            empty when no row names the tree, when the row names no
            branch, as main's own run's does, and when the row could
            not be read.
        reason: Why the record decided nothing: the tree has no id, or
            its row could not be read; empty when the record was read.
    """

    branch: str
    reason: str = ""


def verified_branch(root: Path) -> VerifiedBranch:
    """Read which branch's run proved the tree checked out at *root*.

    Main's run after a squash merge reads it to find the branch whose
    records the tree carries. A row the store cannot read, and a tree
    git cannot name, decide nothing: the branch is empty and the
    reason says why, so the caller reads main's records alone.
    """
    try:
        tree = tree_id(root)
    except git.GitError as error:
        return VerifiedBranch("", f"the tree has no id ({error})")
    row, why = record(root, tree)
    if why:
        return VerifiedBranch("", why)
    return VerifiedBranch(row.branch if row is not None else "")


def record(
    root: Path, tree: str, *, before: datetime | None = None
) -> tuple[Verified | None, str]:
    """The entry for *tree*, or ``(None, reason)``; an absent entry has no reason.

    A read the transport could not answer falls open with its words,
    and so does an entry that is not a row of the record's schema:
    the gate runs, and the line says why the record did not decide.
    With *before*, an entry the store stamped at or after it, or never
    stamped, reads as absent (`written_before`).
    """
    row, why = SERIES.row(state_store(root), tree)
    if row is None:
        return None, why
    if not written_before(row.when, before):
        return None, ""
    return _entry(tree, row.data), ""


def written_before(when: str, before: datetime | None) -> bool:
    """Whether a row stamped *when* counts under the bound *before*.

    Every row counts without a bound. With one, a row counts when the
    store stamped it before the bound; an unstamped row or a stamp
    that does not parse never does. The shards of a check leg read the
    record under one bound, the committer time of the commit they all
    checked out, so a row another run writes while they start counts
    for all of them or for none.
    """
    if before is None:
        return True
    try:
        moment = datetime.fromisoformat(when) if when else None
    except ValueError:
        moment = None
    if moment is None:
        return False
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    bound = before if before.tzinfo is not None else before.replace(tzinfo=UTC)
    return moment < bound


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


def proves(row: Verified, leg: str = "") -> bool:
    """Whether *row* proves its tree for *leg*: a full row naming the leg's platform.

    With *leg* empty, any full row does. Each stored name is read
    through [livery.workshop._speed.leg_label][], so a row written
    with the forge's display names counts for the leg it names, and
    the names and *leg* are compared as platform legs
    ([livery.workshop.platform_leg][]), so a shard of a platform reads
    the row the fold stamped under the platform leg as its own.
    """
    if row.scope != FULL:
        return False
    platform = platform_leg(leg)
    return not leg or platform in {platform_leg(leg_label(name)) for name in row.legs}


def proved(
    root: Path, leg: str = "", *, before: datetime | None = None
) -> tuple[dict[str, Verified], str]:
    """The rows that prove their trees for *leg*, by tree, newest first, in one read.

    ``({}, reason)`` when the record could not be read. With a leg,
    the full rows that name it (`proves`); without one, every full
    row. A row of a narrowed scope proves nothing and is left out, as
    a file that is not a row of the record's schema is. With *before*,
    only the rows the store stamped before it (`written_before`).
    """
    found = SERIES.rows(state_store(root))
    if found.failed:
        return {}, found.reason
    rows = (
        _entry(row.name, row.data)
        for row in found.rows
        if written_before(row.when, before)
    )
    return {entry.tree: entry for entry in rows if proves(entry, leg)}, ""


def held(root: Path, trees: Iterable[str]) -> tuple[set[str], str]:
    """The trees among *trees* the record holds, in one read, whatever legs they name.

    The machine's reflex roots its chain here and asks for no leg: a
    machine is its own platform, and its gate is the reflex, not the
    verdict, so any tree CI's record holds is a root.

    ``(set(), reason)`` when the record could not be read. A file that
    is not a row of the record's schema is skipped the way every read
    skips it, so its tree reads as not held; an absent record holds
    nothing, without a reason.
    """
    found = SERIES.rows(state_store(root))
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

    Only a CI run writes. *legs* are the labels of the run's check
    legs: each ran the full gate, skipped on a full row that named
    it, or ran narrowed on top of *base*, a full row the caller read
    from the record; a composed row names that base and its run. *branch*
    is the pull request branch the run came from, so main's run after
    the squash knows whose records the tree carries. *merged*
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
        state_store(root),
        {tree: entry},
        message=f"verified: tree {tree[:12]} by run {run.run_id}{basis}",
    )


def stamp_from_metrics(root: Path, run: RunContext, *, sha: str) -> str:
    """Stamp the run's tree from what its check legs' rows say they ran.

    Reads the run's file on the metrics series, which the collect
    step put before the verdict; each check leg's row carries the
    scope its marker named and the leg's label, which the stamp
    names ([livery.workshop._speed.leg_of][]), so GitLab's bare job
    name records its platform too. A platform that ran as shards has
    one row, folded under its own name before the collect, and the
    forge's rows of its shards beside it, which carry no scope and are
    skipped ([livery.workshop._speed.forge_shard_row][]). Every leg
    full, or skipped on the record: the tree is stamped naming every
    leg, so after a pull
    request's run on one platform the merge's run leaves a row naming
    every platform. Legs narrowed (``affected``, or ``nothing`` for a prose-only
    diff): the tree is stamped when every narrowed leg measured from
    one tree and the record names that tree as full, and the row
    names that base; otherwise nothing is written and the line says
    which proof is missing. On a merge checkout the row also names
    the two commits merged. Returns the line to print.
    """
    from livery.workshop._metrics import SERIES as METRICS
    from livery.workshop._metrics import run_file

    row, why = METRICS.row(state_store(root), run_file(run.run_id))
    if why:
        return f"  verified: no stamp, {why}"
    if row is None:
        return f"  verified: no stamp, run {run.run_id} left no row"
    jobs = row.data.get("jobs", {})
    if not isinstance(jobs, dict):
        jobs = {}
    # The forge's own rows of a platform's shards carry no scope; the
    # folded row under the platform's name carries what the stamp reads.
    names = sorted(
        name
        for name in jobs
        if name.startswith("check") and not forge_shard_row(name, jobs[name])
    )
    if not names:
        return "  verified: no stamp, the run had no check leg"
    scopes = {
        name: str((jobs[name].get("scope") or {}).get("scope", "unknown"))
        for name in names
    }
    legs = tuple(sorted(leg_of(name, jobs[name]) for name in names))
    unknown = [name for name, scope in scopes.items() if scope not in KNOWN]
    if unknown:
        return f"  verified: no stamp, {', '.join(unknown)} left no scope"
    tree = tree_id(root)
    if all(scope == VERIFIED for scope in scopes.values()):
        return f"  verified: tree {tree[:12]} is already recorded; nothing to stamp"
    try:
        merged = git.merge_parents(root)
    except git.GitError as error:
        return f"  verified: no stamp, HEAD's parents could not be read ({error})"
    if all(scope in (FULL, VERIFIED) for scope in scopes.values()):
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
        name: scope for name, scope in scopes.items() if scope not in (FULL, VERIFIED)
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
