"""Modules measured by coverage.py for real, and a reader of the pages drawn from them.

The reduction's tests share them: the pages drawn from a reduction of
this data must show what coverage.py's own reporters show for it.
``MEASURED`` is the workshop's coverage fixture, line for line: a
partial ``if``, an ``if`` never true, a ``for`` that never starts, a
``# pragma: no cover`` block, a ``# pragma: no branch`` line and a
statement over three lines. ``SHAPES`` adds a class with a nested
function and a lambda, a ``match``, a ``try`` with ``except``, ``else``
and ``finally``, a ``with`` that suppresses an exception, strings over
several lines, and statements over several lines that never run, are
excluded, or branch one way only, and a branch line that always raises.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path

MEASURED = """\
class Tally:
    def __init__(self):
        self.seen = []

    def note(self, value):
        if value:
            self.seen.append(value)


def total(values):
    result = 0
    for value in values:
        result += value
    if result > 100:
        result = 100
    return result


def unused():  # pragma: no cover
    return "never called"


tally = Tally()
tally.note(1)
size = total([])
if size == 0:  # pragma: no branch
    label = "-".join(
        ["no", "values"]
    )
"""

SHAPES = '''\
import contextlib
import sys


class Shelf:
    """A class with methods, a nested function and a lambda."""

    size = 3

    def __init__(self, items):
        self.items = list(items)
        self.key = lambda item: (len(item), item)

    def ordered(self):
        def by_key(items):
            return sorted(items, key=self.key)

        return by_key(self.items)

    def first(self):
        if not self.items:
            return None
        return self.items[0]


def kind(value):
    match value:
        case int():
            return "number"
        case str() if value:
            return "text"
        case _:
            return "other"


def parse(text):
    try:
        number = int(text)
    except ValueError:
        return None
    else:
        return number
    finally:
        pass


def opened(flag):
    with contextlib.suppress(KeyError):
        if flag:
            raise KeyError(flag)
        return "kept"
    return "suppressed"


BANNER = """
a banner over
several lines
"""


SETTINGS = dict(  # pragma: no cover
    verbose=True,
)


def joined(parts):
    return ", ".join(
        part.strip()
        for part in parts
        if part
    )


def never_called():
    return dict(
        a=1,
        b=2,
    )


def strict(value):
    if int(value) > 0:
        return "positive"
    return "other"


def main(mode):
    shelf = Shelf(["pear", "fig"])
    shelf.ordered()
    if (
        shelf.items
    ):
        shelf.first()
    kind(1)
    parse("12")
    opened(False)
    try:
        strict("x")
    except ValueError:
        pass
    if mode == "a":
        kind("x")
        parse("x")
    else:
        opened(True)
        joined(["a ", ""])
    return BANNER


main(sys.argv[1] if len(sys.argv) > 1 else "a")
'''

#: Where the modules live in a member's sources.
SOURCES = "packages/core/src/acme/core"

#: What the measurement reads: branches, and file names from the root.
COVERAGERC = "[run]\nbranch = true\nrelative_files = true\n"


def measure(root: Path) -> None:
    """Write both modules under *root* and measure three runs into ``.coverage``.

    ``measured.py`` runs under no context; ``shapes.py`` runs twice, as
    the contexts ``run-a`` and ``run-b``, taking the other branch of
    its last ``if`` each time. Each run is a fresh interpreter with
    none of the outer gate's meter variables: pytest-cov and
    coverage's subprocess patch would point an inheriting python at the
    live run's data file, and this recording must land in *root*.
    """
    sources = root / SOURCES
    sources.mkdir(parents=True, exist_ok=True)
    (sources / "measured.py").write_text(MEASURED)
    (sources / "shapes.py").write_text(SHAPES)
    (root / ".coveragerc").write_text(COVERAGERC)
    unmetered = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("COVERAGE_", "COV_CORE_"))
    }
    runs: tuple[tuple[list[str], str, list[str]], ...] = (
        ([], "measured.py", []),
        (["--context=run-a"], "shapes.py", ["a"]),
        (["--context=run-b"], "shapes.py", ["b"]),
    )
    for flags, module, arguments in runs:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "coverage",
                "run",
                "--parallel-mode",
                f"--source={SOURCES}",
                *flags,
                f"{SOURCES}/{module}",
                *arguments,
            ],
            cwd=root,
            capture_output=True,
            check=True,
            env=unmetered,
            timeout=60,
        )
    subprocess.run(
        [sys.executable, "-m", "coverage", "combine"],
        cwd=root,
        capture_output=True,
        check=True,
        env=unmetered,
        timeout=60,
    )


class Page(HTMLParser):
    """What a reader sees on a coverage page: its source lines and its table rows.

    A source line is keyed by its number and holds its category, its
    text, each jump's short and long annotation, and the contexts that
    reached it. A table row maps each column's heading to the cell's
    text, the last of a repeated heading winning, and ``href`` to the
    row's first link; ``cells`` holds each row's headings and cells in
    order. Text has its runs of whitespace collapsed.
    """

    def __init__(self) -> None:
        super().__init__()
        self.lines: dict[int, Line] = {}
        self.rows: list[dict[str, str]] = []
        self.cells: list[list[tuple[str, str]]] = []
        self.contexts: list[str] = []
        self._line: Line | None = None
        self._number = 0
        self._depth = 0
        self._jump: list[str] | None = None
        self._why: list[str] | None = None
        self._label: list[str] | None = None
        self._script = False
        self._headings: list[str] = []
        self._cells: list[str] | None = None
        self._heading_row = False
        self._href = ""
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        found = {key: value or "" for key, value in attrs}
        classes = found.get("class", "").split()
        if self._line is not None:
            self._depth += 1
            if "jump" in classes:
                self._jump = []
            elif "why" in classes:
                self._why = []
            elif "ctx" in classes:
                self._label = []
                places = found.get("data-contexts")
                self._line.places = (
                    [int(place) for place in places.split()] if places else None
                )
            return
        if tag == "div" and "ln" in classes:
            category = next(
                (name for name in classes if name not in ("ln", "selected")), ""
            )
            self._line = Line(category)
            self._depth = 0
            self._number = int(found["id"][1:])
        elif tag == "script" and found.get("id") == "contexts":
            self._script = True
            self._text = []
        elif tag == "tr":
            self._cells = []
            self._href = ""
        elif tag in ("td", "th"):
            self._heading_row = tag == "th"
            self._text = []
        elif tag == "a" and self._cells is not None and not self._href:
            self._href = found.get("href", "")

    def handle_data(self, data: str) -> None:
        if self._line is not None:
            self._line.text.append(data)
            if self._why is not None:
                self._why.append(data)
            elif self._jump is not None:
                self._jump.append(data)
            if self._label is not None:
                self._label.append(data)
            return
        self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._line is not None:
            if self._depth == 0:
                self.lines[self._number] = self._line
                self._line = None
                return
            self._depth -= 1
            if self._why is not None:
                self._line.whys.append(_flat(self._why))
                self._why = None
            elif self._jump is not None:
                self._line.jumps.append(_flat(self._jump))
                self._jump = None
            elif self._label is not None:
                self._line.label = _flat(self._label)
                self._label = None
            return
        text = _flat(self._text)
        if self._script and tag == "script":
            self.contexts = json.loads("".join(self._text))
            self._script = False
        elif tag in ("td", "th") and self._cells is not None:
            self._cells.append(text)
        elif tag == "tr" and self._cells is not None:
            if self._heading_row:
                self._headings = self._cells
            else:
                cells = list(zip(self._headings, self._cells, strict=True))
                row = {heading: cell for heading, cell in cells if heading}
                self.rows.append({**row, "href": self._href})
                self.cells.append(cells)
            self._cells = None


class Line:
    """One source line as its page draws it."""

    def __init__(self, category: str) -> None:
        self.category = category
        self.text: list[str] = []
        self.jumps: list[str] = []
        self.whys: list[str] = []
        self.label = ""
        self.places: list[int] | None = None

    def contexts(self, names: list[str]) -> list[str]:
        """The contexts that reached the line, as the page lists them."""
        if self.places is None:
            return [self.label] if self.label else []
        return [names[place] for place in self.places]


def _flat(pieces: list[str]) -> str:
    return " ".join("".join(pieces).split())


def read_page(page: Path) -> Page:
    """The page at *page*, read."""
    reader = Page()
    reader.feed(page.read_text("utf-8"))
    return reader
