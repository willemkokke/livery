"""The local gate record: trees this machine's own ``fm check`` proved green.

``fm submit`` runs the gate before it pushes. When the tree it would
gate is one a green ``fm check`` already proved on this machine, at a
scope that covers what the submit would run, the submit skips its gate
and says which check proved the tree and when. The record lives in the
runner's data directory, is written only by a green local gate on a
clean committed tree, is read only by the submit's gate, and never
leaves the machine: the ``workshop/verified`` record on the forge stays
CI's evidence, and CI never reads or writes this one.

A row names the tree id, the scope (``full``, or ``affected`` with the
packages the gate ran and the base tree the narrowing compared
against), when it was written, and the checkout. The record bounds
itself: a write keeps the newest `KEEP` rows, and a row older than
`MAX_AGE` proves nothing, since the tree id covers the pins in
``uv.lock`` but not the machine's Python or tools, which drift under
the same tree.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import livery.footman as footman
from livery.workshop._git_ops import GitError, GitOps
from livery.workshop._verified import tree_id

#: Rows a write keeps, newest last.
KEEP = 200

#: How long a row proves its tree.
MAX_AGE = timedelta(days=7)

#: The record's file, under the workshop's own folder in the data directory.
FILE = "gate-record.json"

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
        when: When the gate finished, ISO 8601 in UTC.
        root: The checkout the gate ran in, for a reader.
    """

    tree: str
    scope: str
    packages: tuple[str, ...]
    base_tree: str
    when: str
    root: str


def record_path() -> Path:
    """Where the record lives: the workshop's folder in the runner's data directory."""
    return footman.data_dir() / "livery-workshop" / FILE


def read_rows() -> tuple[tuple[Row, ...], str]:
    """Every row, oldest first, or ``((), reason)`` when the file cannot be read."""
    path = record_path()
    if not path.is_file():
        return (), ""
    try:
        loaded = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as error:
        return (), f"the gate record at {path} could not be read ({error})"
    if not isinstance(loaded, list):
        return (), f"the gate record at {path} is not a list of rows"
    rows: list[Row] = []
    for item in loaded:
        if not isinstance(item, dict) or not isinstance(item.get("tree"), str):
            continue
        rows.append(
            Row(
                tree=item["tree"],
                scope=str(item.get("scope", "")),
                packages=tuple(str(p) for p in item.get("packages", []) or []),
                base_tree=str(item.get("base_tree", "")),
                when=str(item.get("when", "")),
                root=str(item.get("root", "")),
            )
        )
    return tuple(rows), ""


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
    scope, and base replaces its predecessor; the newest `KEEP` rows
    are kept.
    """
    if not git.is_clean():
        return "  gate record: the tree has uncommitted changes; nothing recorded"
    try:
        tree = tree_id(git)
        base_tree = _base_tree(git, base) if packages is not None else ""
    except GitError as error:
        return f"  gate record: no tree id ({error}); nothing recorded"
    row = Row(
        tree=tree,
        scope=FULL if packages is None else AFFECTED,
        packages=tuple(packages or ()),
        base_tree=base_tree,
        when=datetime.now(UTC).isoformat(timespec="seconds"),
        root=str(root),
    )
    rows, _why = read_rows()
    kept = [
        r
        for r in rows
        if (r.tree, r.scope, r.base_tree) != (row.tree, row.scope, row.base_tree)
    ]
    kept.append(row)
    kept = kept[-KEEP:]
    path = record_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps([asdict(r) for r in kept], indent=1, sort_keys=True), "utf-8"
        )
    except OSError as error:
        return f"  gate record: {path} could not be written ({error}); nothing recorded"
    return f"  gate record: tree {tree[:12]} recorded as proved green ({row.scope})"


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
    rows, why = read_rows()
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
    for row in rows:
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
