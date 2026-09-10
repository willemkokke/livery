"""The local gate record: trees this checkout's own ``fm check`` proved green.

``fm submit`` runs the gate before it pushes. When the tree it would
gate is one a green ``fm check`` already proved here, at a scope that
covers what the submit would run, the submit skips its gate and says
which check proved the tree and when. The record is a local series of
the state store ([livery.workshop._state][]): it lives in the
checkout's git directory, shared by its worktrees, is written only by
a green local gate on a clean committed tree, is read only by the
submit's gate, and never leaves the machine. The ``workshop/verified``
record on the forge stays CI's evidence, and CI never reads or writes
this one.

A row names the tree id, the scope (``full``, or ``affected`` with the
packages the gate ran and the base tree the narrowing compared
against), and the checkout; the store stamps when it was written. The
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

#: Rows the window keeps.
KEEP = 200

#: How long a row proves its tree.
MAX_AGE = timedelta(days=7)

#: The record: a local series, one row per tree, scope, and base.
SERIES = _state.Series("gate-record", window=KEEP, ci_only=False, local=True)

#: The scopes a row records.
FULL = "full"
AFFECTED = "affected"


@dataclass(frozen=True)
class Row:
    """One proved tree.

    Attributes:
        tree: The git tree id the gate proved.
        scope: ``full``, or ``affected`` for a narrowed gate.
        packages: The packages a narrowed gate ran; empty for ``full``.
        base_tree: The tree a narrowed gate compared against; empty for ``full``.
        when: When the store wrote the row, ISO 8601 in UTC.
        root: The checkout the gate ran in, for a reader.
    """

    tree: str
    scope: str
    packages: tuple[str, ...]
    base_tree: str
    when: str
    root: str


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


def _base_tree(git: GitOps, base: str) -> str:
    return tree_id(git, git.merge_base(base))


def _moment(stamp: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def remember(
    root: Path,
    git: GitOps,
    *,
    packages: tuple[str, ...] | None,
    base: str = "main",
) -> str:
    """Record HEAD's tree as proved green by the gate that just ran; the line to print.

    *packages* is the subset a narrowed gate ran, or None for the
    whole workspace. A dirty tree records nothing: the gate proved the
    working tree, and the id names HEAD's. A row of the same tree,
    scope, and base replaces its predecessor; the window keeps the
    newest `KEEP` rows.
    """
    if not git.is_clean():
        return "  gate record: the tree has uncommitted changes; nothing recorded"
    try:
        tree = tree_id(git)
        base_tree = _base_tree(git, base) if packages is not None else ""
    except GitError as error:
        return f"  gate record: no tree id ({error}); nothing recorded"
    scope = FULL if packages is None else AFFECTED
    row = {
        "tree": tree,
        "scope": scope,
        "packages": list(packages or ()),
        "base_tree": base_tree,
        "root": str(root),
    }
    why = SERIES.put(
        root,
        {_name(tree, scope, base_tree): row},
        message=f"gate record: tree {tree[:12]} proved green ({scope})",
    )
    if why:
        return f"  gate record: {why}; nothing recorded"
    return f"  gate record: tree {tree[:12]} recorded as proved green ({scope})"


def covering(
    git: GitOps,
    *,
    affected: bool,
    base: str = "main",
    now: datetime | None = None,
) -> tuple[Row | None, str]:
    """The newest row proving HEAD's tree for the gate a submit would run.

    A full row proves any gate; an affected row proves an affected gate
    against the same base tree. A dirty tree, another tree, a row older
    than `MAX_AGE`, or a record that cannot be read proves nothing, and
    the reason comes back when there is one to print.
    """
    if not git.is_clean():
        return None, "the tree has uncommitted changes"
    try:
        tree = tree_id(git)
    except GitError as error:
        return None, f"no tree id ({error})"
    found, why = rows(git.root)
    if why:
        return None, why
    base_tree = ""
    if affected:
        try:
            base_tree = _base_tree(git, base)
        except GitError:
            base_tree = ""
    moment = now or datetime.now(UTC)
    best: Row | None = None
    for row in found:
        if row.tree != tree:
            continue
        when = _moment(row.when)
        if when is None or moment - when > MAX_AGE:
            continue
        full = row.scope == FULL
        same_base = bool(base_tree) and row.base_tree == base_tree
        covers = full or (affected and row.scope == AFFECTED and same_base)
        if covers and (best is None or row.when > best.when):
            best = row
    return best, ""
