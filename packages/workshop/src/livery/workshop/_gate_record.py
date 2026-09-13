"""The local gate record: the chain of trees this checkout's green gates proved.

Every green local gate writes one row: the tree it proved, and the
proved tree it started from. A full gate proves a tree outright. A
narrowed gate proves the packages a delta can influence, so its row
names the tree the delta was taken from, and the tree is proved when
that one is: a package the delta did not touch keeps the earlier
proof, since ``affected`` is closed under dependents. A chain of such
rows is a proof of its newest tree once it reaches a root, a full row
or a tree CI's own ``workshop/verified`` record holds, which is what
the merge base with the base branch is after a merge.

Two readers use the chain. The reflex, ``fm check`` on a machine,
looks for the nearest proved tree in the checkout's history and gates
the delta from it to the working tree, so the tenth commit of a
branch pays for what the tenth commit touched; a working tree the
chain already proves pays nothing. ``fm submit`` walks the chain from
HEAD's tree and skips its gate when the chain reaches a root, saying
so. The rows key the working tree's id, which a commit that takes
everything shares, so a check before the commit proves the commit.

The record is a local series of the state store
([livery.workshop._state][]): it lives in the checkout's git
directory, shared by its worktrees, is written only by a green local
gate, and never leaves the machine. CI never reads or writes it. The
window keeps the newest `KEEP` rows, and a row older than `MAX_AGE`
proves nothing, since the tree id covers the pins in ``uv.lock`` but
not the machine's Python or tools, which drift under the same tree.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from livery.workshop import _state
from livery.workshop._git_ops import GitError, GitOps
from livery.workshop._verified import tree_id

#: How many rows the record keeps, newest first.
KEEP = 200
#: How long a row proves its tree.
MAX_AGE = timedelta(days=7)
#: How far back HEAD's first-parent history is searched for a proved tree.
REACH = 50

#: The newest rows whose trees the plan measures as step bases, so a
#: green check of a dirty tree is the next check's base after one more
#: edit, without a diff per row of the whole window.
NEAR = 20

SERIES = _state.Series("gate-record", window=KEEP, ci_only=False, local=True)

#: The scopes: a full gate roots a chain; a step gates the delta from
#: ``base_tree``. ``affected`` is the step of an earlier spelling,
#: whose ``base_tree`` was the merge base with the base branch.
FULL = "full"
STEP = "step"
AFFECTED = "affected"


@dataclass(frozen=True)
class Row:
    """One proved tree.

    Attributes:
        tree: The git tree id the gate proved.
        scope: ``full``, or ``step`` (``affected`` in the earlier
            spelling) for a gate of the delta from ``base_tree``.
        packages: The packages a step gated; empty for ``full``, and
            for a step whose delta reached no package.
        base_tree: The proved tree a step's delta was taken from;
            empty for ``full``.
        when: When the store wrote the row, ISO 8601 in UTC.
        root: The checkout the gate ran in, for a reader.
    """

    tree: str
    scope: str
    packages: tuple[str, ...]
    base_tree: str
    when: str
    root: str


@dataclass(frozen=True)
class Chain:
    """A proof of one tree: its steps back to a root.

    Attributes:
        tree: The tree the chain proves.
        steps: The rows walked, newest first; empty when CI's record
            holds the tree itself.
        root: ``full`` when the chain ends on a full row, ``verified``
            when it rests on a tree CI's record holds.
        root_tree: The tree the chain rests on.
    """

    tree: str
    steps: tuple[Row, ...]
    root: str
    root_tree: str

    def describe(self) -> str:
        """One clause a line can carry: the steps and the root."""
        rooted = (
            f"a full gate of {self.root_tree[:12]}"
            if self.root == FULL
            else f"CI's record of {self.root_tree[:12]}"
        )
        count = len(self.steps)
        if self.root == FULL:
            count -= 1
        steps = f"{count} step(s) on " if count else ""
        return f"{steps}{rooted}"


@dataclass(frozen=True)
class Plan:
    """What the reflex runs for the working tree.

    Attributes:
        mode: ``proved`` when the chain already proves the working
            tree, ``step`` when a proved tree is in reach and the delta
            from it is what runs, ``full`` when none is.
        tree: The working tree's id, the row a green run records.
        base_tree: The proved tree a step builds on; empty otherwise.
        paths: The paths a step's delta touches.
        why: The reason, for the line the gate prints.
    """

    mode: str
    tree: str
    base_tree: str = ""
    paths: tuple[str, ...] = ()
    why: str = ""


def rows(root: Path) -> tuple[tuple[Row, ...], str]:
    """Every row, newest first, or ``((), reason)`` when the record cannot be read.

    A row without a tree is not a proof and is left out; the store
    already skipped what does not parse or is of another schema.
    """
    found = SERIES.rows(root)
    if found.failed:
        return (), found.reason
    kept: list[Row] = []
    for item in found.rows:
        tree = item.data.get("tree")
        if not isinstance(tree, str):
            continue
        kept.append(
            Row(
                tree=tree,
                scope=str(item.data.get("scope", "")),
                packages=tuple(str(p) for p in item.data.get("packages", []) or []),
                base_tree=str(item.data.get("base_tree", "")),
                when=item.when,
                root=str(item.data.get("root", "")),
            )
        )
    return tuple(kept), ""


def _name(tree: str, scope: str, base_tree: str) -> str:
    """The row's file: tree, scope, and base, so a repeat replaces its predecessor."""
    return f"{tree}--{scope}" + (f"--{base_tree}" if base_tree else "")


def _moment(stamp: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _fresh(row: Row, now: datetime) -> bool:
    when = _moment(row.when)
    return when is not None and now - when <= MAX_AGE


def remember(
    root: Path,
    git: GitOps,
    *,
    tree: str,
    packages: tuple[str, ...] | None,
    base_tree: str = "",
) -> str:
    """Record *tree* as proved green by the gate that just ran; the line to print.

    *packages* is what a step gated, with *base_tree* the proved tree
    its delta was taken from; ``None`` records a full gate. A row of
    the same tree, scope, and base replaces its predecessor; the
    window keeps the newest `KEEP` rows.
    """
    del git  # the seam every writer of the record takes, for the callers' symmetry
    scope = FULL if packages is None else STEP
    base = base_tree if packages is not None else ""
    row = {
        "tree": tree,
        "scope": scope,
        "packages": list(packages or ()),
        "base_tree": base,
        "root": str(root),
    }
    why = SERIES.put(
        root,
        {_name(tree, scope, base): row},
        message=f"gate record: tree {tree[:12]} proved green ({scope})",
    )
    if why:
        return f"  gate record: {why}; nothing recorded"
    return f"  gate record: tree {tree[:12]} recorded as proved green ({scope})"


def chain(
    git: GitOps,
    tree: str,
    *,
    roots: set[str],
    now: datetime | None = None,
    found: tuple[Row, ...] | None = None,
) -> Chain | None:
    """The chain proving *tree*, or ``None`` when the rows do not reach a root.

    *roots* are the trees CI's record holds. Rows older than `MAX_AGE`
    do not count; a row of an earlier spelling counts as a step. A
    cycle or a base no row proves ends the walk empty. *found* are the
    rows already read, for a caller walking many trees; the record is
    read once here otherwise.
    """
    if found is None:
        found, why = rows(git.root)
        if why:
            return None
    moment = now or datetime.now(UTC)
    by_tree: dict[str, list[Row]] = {}
    for row in found:
        if _fresh(row, moment):
            by_tree.setdefault(row.tree, []).append(row)
    steps: list[Row] = []
    current = tree
    seen: set[str] = set()
    while current not in seen:
        seen.add(current)
        candidates = by_tree.get(current, [])
        full = [row for row in candidates if row.scope == FULL]
        if full:
            steps.append(max(full, key=lambda row: row.when))
            return Chain(tree, tuple(steps), FULL, current)
        if current in roots:
            return Chain(tree, tuple(steps), "verified", current)
        links = [
            row for row in candidates if row.scope in (STEP, AFFECTED) and row.base_tree
        ]
        if not links:
            return None
        step = max(links, key=lambda row: row.when)
        steps.append(step)
        current = step.base_tree
    return None


def verified_roots(root: Path, git: GitOps, base: str) -> tuple[set[str], str]:
    """The trees CI's record holds that a chain may rest on, and why none when none.

    The candidates are the merge base with *base* and HEAD's
    first-parent history, `REACH` commits deep. After a merge, main's
    run stamps its tree, so a branch cut from it starts proved; while
    that run is still red or unfinished, the parent's tree carries the
    chain and the merge's own changes ride in the first step. A record
    the forge could not answer leaves the chain rootless, and the
    reason is returned for the line.
    """
    from livery.workshop import _verified
    from livery.workshop._state import remote_snapshot

    try:
        base_tree = tree_id(git, git.merge_base(base))
    except GitError as error:
        return set(), f"no merge base with origin/{base} ({error})"
    history = git.first_parent_trees(REACH)
    with remote_snapshot(root, fetch=("verified",)):
        roots, why = _verified.held(root, [base_tree, *history])
    if roots:
        return roots, ""
    if why:
        return set(), f"CI's record could not be read ({why})"
    return set(), (
        f"CI's record holds neither the merge base's tree {base_tree[:12]}"
        f" nor one of HEAD's last {len(history)} first-parent trees"
    )


def covering(
    git: GitOps, *, base: str = "main", now: datetime | None = None
) -> tuple[Chain | None, str]:
    """The chain proving HEAD's tree for the gate a submit would run, or why not."""
    try:
        tree = tree_id(git)
    except GitError as error:
        return None, f"no tree id ({error})"
    roots, why = verified_roots(git.root, git, base)
    proof = chain(git, tree, roots=roots, now=now)
    if proof is not None:
        return proof, ""
    reason = f"no chain of green gates reaches tree {tree[:12]}"
    return None, f"{reason} ({why})" if why else reason


def plan(
    root: Path, git: GitOps, *, base: str = "main", now: datetime | None = None
) -> Plan:
    """What the reflex runs for the working tree: nothing, a step, or everything.

    The working tree's own chain settles it first. Else the proved
    tree with the fewest paths changed to the working tree becomes the
    step's base, chosen among HEAD's first-parent history, `REACH`
    commits deep, and the trees of the newest `NEAR` rows, so a green
    check of a dirty tree is the base of the next check after one more
    edit; a row's tree git no longer has is passed over. With none in
    reach the whole gate runs and roots a new chain.
    """
    tree = git.working_tree_id()
    roots, why = verified_roots(root, git, base)
    found, unread = rows(root)
    moment = now or datetime.now(UTC)
    proof = chain(git, tree, roots=roots, now=now, found=found)
    if proof is not None:
        return Plan("proved", tree, why=proof.describe())
    near = list(dict.fromkeys(row.tree for row in found if _fresh(row, moment)))
    best: tuple[int, str, tuple[str, ...]] | None = None
    for candidate in dict.fromkeys([*git.first_parent_trees(REACH), *near[:NEAR]]):
        if candidate == tree:
            continue
        if chain(git, candidate, roots=roots, now=now, found=found) is None:
            continue
        try:
            paths = tuple(git.tree_diff(candidate, tree))
        except GitError:
            continue
        if best is None or len(paths) < best[0]:
            best = (len(paths), candidate, paths)
    if best is not None:
        return Plan("step", tree, base_tree=best[1], paths=best[2])
    return Plan("full", tree, why=why or unread or "no proved tree in reach")
