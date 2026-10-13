"""A reader of the coverage pages: what a person sees on an index and on a file's page.

The coverage pins share it, so each asserts what a page shows rather
than how its markup spells it.
"""

from __future__ import annotations

import json
from html.parser import HTMLParser
from pathlib import Path


class Line:
    """One source line as its page draws it.

    Attributes:
        category: The category it is drawn in, empty for none.
        text: Its text, the annotations and labels beside it included.
        jumps: Each untaken jump's short annotation.
        whys: Each untaken jump's sentence.
        source: Its source text.
        runs: How many times it ran, as the page shows it, for a
            measurer that counts runs.
        label: Its contexts label: ``(empty)`` or ``N ctx``.
        places: The places in the page's contexts its label lists.
    """

    def __init__(self, category: str) -> None:
        self.category = category
        self.text: list[str] = []
        self.jumps: list[str] = []
        self.whys: list[str] = []
        self.source = ""
        self.runs = ""
        self.label = ""
        self.places: list[int] = []

    def contexts(self, names: list[str]) -> list[str]:
        """The contexts that reached the line, as the page lists them."""
        if not self.places:
            return [self.label] if self.label else []
        return [names[place] for place in self.places]

    @property
    def shown(self) -> str:
        """The line's text with its runs of whitespace collapsed."""
        return _flat(self.text)


class Page(HTMLParser):
    """A coverage page as read: its lines, its rows, and what its header says.

    A table row maps each column's heading to the cell's text, the last
    of a repeated heading winning, and ``href`` to the row's first link;
    ``cells`` holds each row's headings and cells in order. Text has its
    runs of whitespace collapsed.
    """

    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self.lines: dict[int, Line] = {}
        self.rows: list[dict[str, str]] = []
        self.cells: list[list[tuple[str, str]]] = []
        self.contexts: list[str] = []
        self.notes: list[str] = []
        self.absent: list[str] = []
        self.notice = ""
        self.floor = ""
        self.body: dict[str, str] = {}
        self._line: Line | None = None
        self._number = 0
        self._depth = 0
        self._open: list[tuple[str, list[str]]] = []
        self._section = ""
        self._headings: list[str] = []
        self._row: list[str] | None = None
        self._heading_row = False
        self._href = ""
        self._text: list[str] = []
        self._capture: tuple[str, str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        found = {key: value or "" for key, value in attrs}
        classes = found.get("class", "").split()
        if tag == "body":
            self.body = found
        if self._line is not None:
            self._depth += 1
            role = next(
                (
                    name
                    for name in ("why", "jump", "ctx", "runs", "src")
                    if name in classes
                ),
                "",
            )
            if role == "ctx" and found.get("data-contexts"):
                self._line.places = [
                    int(place) for place in found["data-contexts"].split()
                ]
            self._open.append((role, []))
            return
        if tag == "div" and "ln" in classes:
            category = next(
                (name for name in classes if name not in ("ln", "selected")), ""
            )
            self._line = Line(category)
            self._number = int(found["id"][1:])
            self._depth = 0
            self._open = []
            return
        if tag == "section":
            self._section = classes[0] if classes else "section"
        if tag == "ul" and "about" in classes:
            self._section = "about"
        if tag == "tr":
            self._row = []
            self._href = ""
        elif tag in ("td", "th"):
            self._heading_row = tag == "th"
            self._text = []
        elif tag == "a" and self._row is not None and not self._href:
            self._href = found.get("href", "")
        if self._capture is not None:
            return
        kind = ""
        if tag == "title":
            kind = "title"
        elif tag == "script" and found.get("id") == "contexts":
            kind = "contexts"
        elif tag == "p" and "notice" in classes:
            kind = "notice"
        elif tag == "p" and "floor" in classes:
            kind = "floor"
        elif (tag == "li" and self._section == "about") or (
            tag == "p" and "about" in classes
        ):
            kind = "note"
        elif tag == "code" and self._section == "absent":
            kind = "absent"
        if kind:
            self._capture = (kind, tag)
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._line is not None:
            self._line.text.append(data)
            for role, held in self._open:
                if role:
                    held.append(data)
            return
        self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._line is not None:
            if self._depth == 0:
                self.lines[self._number] = self._line
                self._line = None
                return
            self._depth -= 1
            role, held = self._open.pop()
            text = _flat(held)
            if role == "why":
                self._line.whys.append(text)
                # The sentence sits inside its jump: the jump's own text
                # is what came before it.
                if self._open and self._open[-1][0] == "jump":
                    jump = self._open[-1][1]
                    del jump[len(jump) - len(held) :]
            elif role == "jump":
                self._line.jumps.append(text)
            elif role == "ctx":
                self._line.label = text
            elif role == "runs":
                self._line.runs = text
            elif role == "src":
                self._line.source = text
            return
        text = _flat(self._text)
        if tag in ("section", "ul"):
            self._section = ""
        if self._capture is not None and self._capture[1] == tag:
            kind = self._capture[0]
            self._capture = None
            if kind == "title":
                self.title = text
            elif kind == "contexts":
                self.contexts = json.loads("".join(self._text))
            elif kind == "notice":
                self.notice = text
            elif kind == "floor":
                self.floor = text
            elif kind == "note":
                self.notes.append(text)
            else:
                self.absent.append(text)
        elif tag in ("td", "th") and self._row is not None:
            self._row.append(text)
        elif tag == "tr" and self._row is not None:
            if self._heading_row:
                self._headings = self._row
            else:
                cells = list(zip(self._headings, self._row, strict=True))
                row = {heading: cell for heading, cell in cells if heading}
                self.rows.append({**row, "href": self._href})
                self.cells.append(cells)
            self._row = None


def _flat(pieces: list[str]) -> str:
    return " ".join("".join(pieces).split())


def read_page(page: Path) -> Page:
    """The page at *page*, read."""
    reader = Page()
    reader.feed(page.read_text("utf-8"))
    return reader
