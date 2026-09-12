"""``fm store.ls`` and ``fm store.show``: the state store, read by hand.

Every row the workshop keeps is a declared series of the state store
([livery.workshop._state][]), gathered in one place
([livery.workshop._series][]). ``store.ls`` names each series and
family with its scope, its window, and what it holds; ``store.show``
prints a series' rows newest first through the same reader the
verdicts use, so what a person reads is what the gate judged. Both
only read: the gate's verbs write, and the janitor bounds.
"""

from __future__ import annotations

import json as _json
from pathlib import Path
from typing import Annotated, Any

from livery.footman import doc, fail, group
from livery.workshop._layers import workspace_root
from livery.workshop._state import WHOLE, Keyed, Series, remote_snapshot

store = group("store", help="The state store, read by hand")


def _root() -> Path:
    root = workspace_root()
    if root is None:
        fail("no workspace: no workshop.toml above the working directory")
    return root


def _declared() -> tuple[Series | Keyed, ...]:
    from livery.workshop._series import DECLARED

    return DECLARED


def _scope(item: Series | Keyed) -> str:
    window = "no window" if item.window is None else f"window {item.window}"
    return f"{'local' if item.local else 'remote'}, {window}"


def ls_flow(root: Path) -> list[str]:
    """One line per declared series or family: scope, window, and what it holds.

    One listing of the remote namespace and one fetch of what the
    checkout lacks serve every series, so the call costs two round
    trips whatever the store holds.
    """
    with remote_snapshot(root, fetch=WHOLE):
        return _ls_lines(root)


def _ls_lines(root: Path) -> list[str]:
    lines: list[str] = []
    for item in _declared():
        if isinstance(item, Series):
            found = item.rows(root)
            if found.failed:
                held = found.reason
            else:
                held = f"{len(found.rows)} row(s)"
                if found.skipped:
                    held += f", {len(found.skipped)} skipped"
            lines.append(f"  {item.name}: {_scope(item)}; {held}")
            continue
        keys = item.listed(root)
        held = "could not be listed" if keys is None else f"{len(keys)} key(s)"
        spelled = "/".join(f"<{part}>" for part in item.keys)
        lines.append(f"  {item.name}/{spelled}: {_scope(item)}; {held}")
    return lines


def _compact(fields: dict[str, Any]) -> str:
    """The row's fields on one line: scalars as they are, containers by size."""
    parts: list[str] = []
    for name, value in sorted(fields.items()):
        if isinstance(value, dict):
            parts.append(f"{name}: {{{len(value)} entries}}")
        elif isinstance(value, list):
            parts.append(f"{name}: [{len(value)} items]")
        else:
            parts.append(f"{name}: {value}")
    return ", ".join(parts)


def show_flow(
    root: Path, name: str, *, key: str = "", as_json: bool = False
) -> list[str]:
    """A series' rows newest first, a family's by its key, or the rows as JSON.

    Refuses an unknown name (naming the known ones), a family without
    its key (listing the keys it holds), a key of the wrong shape, and
    a series the store cannot read (with the store's reason). The
    read goes through one listing of the remote namespace.
    """
    with remote_snapshot(root, fetch=(name,)):
        return _show_lines(root, name, key=key, as_json=as_json)


def _show_lines(
    root: Path, name: str, *, key: str = "", as_json: bool = False
) -> list[str]:
    declared = _declared()
    item = next((each for each in declared if each.name == name), None)
    if item is None:
        fail(
            f"no series named {name!r}; the series are"
            f" {', '.join(each.name for each in declared)}"
        )
    parts = tuple(part for part in key.split(",") if part) if key else ()
    if isinstance(item, Keyed):
        if not parts:
            keys = item.listed(root)
            if keys is None:
                fail(f"{item.name}: its keys could not be listed")
            spelled = ",".join(f"<{part}>" for part in item.keys)
            lines = [f"  {item.name} is keyed; --key={spelled} picks one of:"]
            lines += [f"    {','.join(found)}" for found in keys] or ["    (none yet)"]
            return lines
        if len(parts) != len(item.keys):
            fail(
                f"{item.name} is keyed by {', '.join(item.keys)}; --key names"
                f" {len(parts)} part(s)"
            )
        series = item.at(*parts)
    elif parts:
        fail(f"{item.name} is not keyed; drop --key")
    else:
        series = item
    found = series.rows(root)
    if found.failed:
        fail(found.reason)
    if as_json:
        rows = [{"name": row.name, **row.data} for row in found.rows]
        return [_json.dumps(rows, indent=1, sort_keys=True)]
    lines = [f"  {series.ref}: {len(found.rows)} row(s)"]
    for row in found.rows:
        fields = {k: v for k, v in row.data.items() if k not in ("schema", "when")}
        lines.append(f"  {row.name}  {row.when or '(unstamped)'}")
        lines.append(f"    {_compact(fields)}")
    lines.extend(f"  {skipped}" for skipped in found.skipped)
    return lines


@store.task(name="ls")
def store_ls() -> None:
    """List every series the workshop keeps: its scope, its window, what it holds.

    A remote series is read from origin, a local one from this
    checkout's git directory; a series that cannot be read prints the
    store's reason in its place.
    """
    for line in ls_flow(_root()):
        print(line)


@store.task(name="show")
def store_show(
    series: Annotated[str, doc("the series' name, as store.ls lists it")],
    key: Annotated[str, doc("a family's key, its parts comma-separated")] = "",
    json: Annotated[bool, doc("the rows as JSON, newest first")] = False,
) -> None:
    """Print a series' rows newest first, through the reader the verdicts use.

    Each row prints as its file name and stamp, then its fields on one
    line with containers by size; ``--json`` prints every field as it
    is. A family (``store.ls`` shows its key parts) is shown one key at
    a time, and without ``--key`` lists the keys it holds.
    """
    for line in show_flow(_root(), series, key=key, as_json=json):
        print(line)
