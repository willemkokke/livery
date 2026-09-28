"""The content a repository owns inside its managed files.

A managed file is rendered whole and judged byte for byte, and a
repository still needs lines of its own in some of them: its own
rules in the root ``.gitignore``, its own tables in ``pyproject.toml``,
its own tasks below the mount in ``tasks.py``. Two forms carry them.

A **region** is a pair of marker comments the template renders, with
the repository's lines between them. The render reads each region
from the committed file as an input, the way it reads the answers,
and writes it back in place, so an apply preserves it by
construction and the drift gate still compares whole bytes: an edit
inside a region is the repository's, an edit outside it is drift,
and a removed marker is drift too. A file without the region yet
gains the markers, empty, on the next apply.

A **tail** serves a format without comments: the render owns the
first lines, as many as it renders, and the repository's lines
follow. A changed prefix is drift the same way.

The markers are one shape in every comment style, so a reader who
has seen one has seen them all::

    # -- workshop: region tables, yours to edit; the render keeps it --
    # -- workshop: end tables --
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

#: The marker lines, formatted with the file's comment leader and the
#: region's name. A template writes them verbatim, so the parser and
#: the template agree by this one spelling.
OPEN = "{leader} -- workshop: region {name}, yours to edit; the render keeps it --"
CLOSE = "{leader} -- workshop: end {name} --"

_OPEN = re.compile(r"^\s*(?:#|//)\s*-- workshop: region ([A-Za-z0-9_-]+), .*--\s*$")
_CLOSE = re.compile(r"^\s*(?:#|//)\s*-- workshop: end ([A-Za-z0-9_-]+) --\s*$")


@dataclass(frozen=True)
class Region:
    """One region as a committed file carries it.

    Attributes:
        name: The region's name, as its markers spell it.
        content: The lines between the markers, newline-terminated
            when there are any, empty otherwise.
        first: The line number of the opening marker, one-based.
        last: The line number of the closing marker, one-based.
    """

    name: str
    content: str
    first: int
    last: int


def regions_in(text: str) -> tuple[Region, ...]:
    """Every region *text* carries, in file order.

    A marker without its partner is not a region: the drift gate
    reports the file, since the markers are rendered bytes.
    """
    found: list[Region] = []
    lines = text.split("\n")
    open_name = ""
    start = 0
    body: list[str] = []
    for number, line in enumerate(lines, start=1):
        opened = _OPEN.match(line)
        closed = _CLOSE.match(line)
        if opened and not open_name:
            open_name, start, body = opened.group(1), number, []
            continue
        if closed and open_name and closed.group(1) == open_name:
            content = "".join(f"{item}\n" for item in body)
            found.append(Region(open_name, content, start, number))
            open_name = ""
            continue
        if open_name:
            body.append(line)
    return tuple(found)


def region_names(text: str) -> tuple[str, ...]:
    """The names the markers in *text* open, whether or not they close."""
    return tuple(
        m.group(1) for m in (_OPEN.match(line) for line in text.split("\n")) if m
    )


def contents(path: Path) -> dict[str, str]:
    """The committed file's regions, name to content; empty when the file is absent."""
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8")
    return {region.name: region.content for region in regions_in(text)}


def unmatched(text: str) -> tuple[str, ...]:
    """The names whose markers do not pair up in *text*."""
    paired = {region.name for region in regions_in(text)}
    opened = set(region_names(text))
    closed = {
        m.group(1) for m in (_CLOSE.match(line) for line in text.split("\n")) if m
    }
    return tuple(sorted((opened | closed) - paired))


def marker_lines(leader: str, name: str) -> tuple[str, str]:
    """The opening and closing marker for *name* under *leader*."""
    return OPEN.format(leader=leader, name=name), CLOSE.format(leader=leader, name=name)


def tail_split(committed: bytes, rendered: bytes) -> tuple[bytes, bytes]:
    """*committed* as the render's own lines and the repository's tail.

    The render owns as many lines as it renders; what follows is the
    repository's. Returns ``(prefix, tail)``.
    """
    count = rendered.count(b"\n") + (
        0 if rendered.endswith(b"\n") or not rendered else 1
    )
    lines = committed.split(b"\n")
    head = b"\n".join(lines[:count])
    if count and len(lines) > count:
        head += b"\n"
    return head, committed[len(head) :]
