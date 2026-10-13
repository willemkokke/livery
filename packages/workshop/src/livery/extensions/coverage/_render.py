"""Draw one package's coverage pages from its files' readings in the line format.

[livery.extensions.coverage._render.render][] writes a package's tree:
an index of its files, an index of its functions and one of its
classes, a page per file with its source highlighted line by line, and
the stylesheet and script every page shares, which ship in this
extension's ``content/assets``. The numbers are coverage.py's
([livery.extensions.coverage._format.numbers][]): the statements,
branches and percentages its report shows, and the category it draws
each line in. Beyond them, a page shows how many times each line ran
for a measurer that counts runs, a measurer's numbered branches and
its counts of a line's branches, the code of a line that ran which
never did for a measurer that reads columns, who measured each file,
where the measurement comes from, and the package's floor. The pages
depend on the readings alone: drawing the same readings again writes
the same tree.
"""

from __future__ import annotations

import bisect
import hashlib
import html
import itertools
import json
import re
import shutil
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from livery.extensions.coverage._format import (
    NO_BRANCHES,
    TARGETS,
    THROW,
    WAIVED,
    Branch,
    Entry,
    Merged,
    Numbers,
    numbers,
    region_rows,
    shown,
    unrun_columns,
)

#: The stylesheet and the script every page loads, shipped beside this
#: module and copied into each tree.
ASSETS = Path(__file__).resolve().parent / "content" / "assets"
STYLE = "coverage.css"
SCRIPT = "coverage.js"

#: The regions a page indexes, each kind with its plural.
REGION_KINDS = (("function", "functions"), ("class", "classes"))

#: The category a line is drawn in: excluded, missing, partial or run,
#: and the same four for the later lines of a statement written over
#: several lines.
CATEGORIES = ("exc", "mis", "par", "run", "exc2", "mis2", "par2", "run2")

#: The long annotation of a line none of whose branches ran.
ANYWHERE = "line {line} didn't jump anywhere: it always raised an exception."

#: The label of a line reached by the context outside every test alone.
EMPTY_CONTEXT = "(empty)"

#: The arrow coverage.py's pages draw between a line and a jump that
#: never ran, with the narrow spaces around it.
ARROW = (
    "\N{NARROW NO-BREAK SPACE}\N{RIGHTWARDS ARROW WITH STROKE}\N{NARROW NO-BREAK SPACE}"
)


@dataclass(frozen=True)
class Drawn:
    """One file to draw.

    Attributes:
        name: The file's path from the workspace root.
        merged: Its merged reading.
        text: The checkout's text of the file when it is the content
            measured; None when the checkout holds other content.
        digest: The checkout's git blob id of the file.
    """

    name: str
    merged: Merged
    text: str | None
    digest: str = ""


@dataclass(frozen=True)
class Report:
    """One package's pages: what to draw, and what to say about it.

    Attributes:
        title: What the pages cover: ``packages/workshop``.
        files: The files to draw.
        contexts: The contexts a reading's ``reached`` indexes name.
        notes: Lines the index states under its heading: where the
            measurement comes from, and the commit drawn.
        absent: The measured files the checkout no longer has.
        precision: The decimals a percentage shows.
        floor: The line each index states under its heading about the
            package's floor; empty for none.
    """

    title: str
    files: tuple[Drawn, ...]
    contexts: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    absent: tuple[str, ...] = ()
    precision: int = 0
    floor: str = ""


def render(into: Path, report: Report) -> Numbers:
    """Write *report*'s pages into *into*, replacing what was there; the totals.

    *into* is removed and written afresh, so a file that left the
    report leaves no page behind. Each page is written with LF line
    endings, so one report gives the same bytes on every platform.
    """
    shutil.rmtree(into, ignore_errors=True)
    into.mkdir(parents=True)
    assets = {name: _copy_asset(into, name) for name in (STYLE, SCRIPT)}
    files = sorted(report.files, key=lambda drawn: drawn.name)
    pages = [page_name(drawn.name) for drawn in files]
    totals = Numbers()
    rows: list[tuple[Drawn, Numbers]] = []
    for drawn in files:
        counted = numbers(drawn.merged.entry, drawn.merged.measurer)
        rows.append((drawn, counted))
        totals += counted
    kinds = [
        (noun, plural)
        for noun, plural in REGION_KINDS
        if any(noun in drawn.merged.measurer.regions for drawn in files)
    ]
    branched = any(_has_branches(drawn) for drawn in files)
    page = _Pages(report, assets, branched, kinds)
    for index, (drawn, counted) in enumerate(rows):
        before = pages[index - 1] if index else "index.html"
        after = pages[index + 1] if index + 1 < len(pages) else "index.html"
        _write(into / pages[index], page.file(drawn, counted, before, after))
    first = pages[0] if pages else "index.html"
    last = pages[-1] if pages else "index.html"
    index_rows = [
        _Row(drawn.name, pages[index], "", "", counted)
        for index, (drawn, counted) in enumerate(rows)
    ]
    _write(into / "index.html", page.index("file", index_rows, totals, last, first))
    for noun, _plural in kinds:
        region_index: list[_Row] = []
        for index, (drawn, _counted) in enumerate(rows):
            if noun not in drawn.merged.measurer.regions:
                continue
            for region, narrowed in region_rows(drawn.merged.entry, noun):
                counted = numbers(narrowed, drawn.merged.measurer)
                if region is None:
                    region_index.append(
                        _Row(drawn.name, pages[index], f"(no {noun})", "", counted)
                    )
                    continue
                region_index.append(
                    _Row(
                        drawn.name,
                        f"{pages[index]}#t{region.start}",
                        region.name,
                        region.name.rpartition(".")[2].lstrip("_"),
                        counted,
                    )
                )
        _write(
            into / f"{noun}_index.html",
            page.index(noun, region_index, totals, last, first),
        )
    return totals


def _write(path: Path, markup: str) -> None:
    """Write a page's *markup* to *path* as UTF-8 with LF line endings."""
    path.write_text(markup, encoding="utf-8", newline="\n")


def page_name(name: str) -> str:
    """The page drawn for the file at *name*: its name, and its directory's digest."""
    path = PurePosixPath(name)
    folder = hashlib.sha1(
        str(path.parent).encode("utf-8"), usedforsecurity=False
    ).hexdigest()[:8]
    stem = re.sub(r"[^A-Za-z0-9_-]", "_", path.name)
    return f"f_{folder}_{stem}.html"


def line_categories(entry: Entry, count: int) -> list[str]:
    """The category of each line from 1 to *count*, as coverage.py draws it.

    Excluded first, then missing, then partial, then run; a later line
    of a statement written over several lines takes its first line's
    category with a ``2``, and any other line has none, an empty string.
    """
    missing = entry.missing
    partial = entry.partial
    first_of = {
        line: first for first, last in entry.spans for line in range(first, last + 1)
    }
    found: list[str] = []
    for line in range(1, count + 1):
        own = _category(line, entry, missing, partial)
        if own:
            found.append(own)
            continue
        first = first_of.get(line)
        if first is None:
            found.append("")
            continue
        of_first = _category(first, entry, missing, partial)
        found.append(f"{of_first}2" if of_first else "")
    return found


def _category(
    line: int, entry: Entry, missing: frozenset[int], partial: frozenset[int]
) -> str:
    if line in entry.excluded:
        return "exc"
    if line in missing:
        return "mis"
    if line in partial:
        return "par"
    if line in entry.statements:
        return "run"
    return ""


def jumps(merged: Merged, line: int) -> list[tuple[str, str]]:
    """The jumps from *line* that never ran: each one's short and long annotation.

    coverage.py's wording: ``12 ↛ 15`` and ``12 ↛ exit`` beside the
    sentence that says why, or ``12 ↛ anywhere`` when no branch of the
    line ran. A measurer that numbers branches names them ``branch 1``,
    an exception's path ``branch 1 (throw)``, and one that counts a
    line's branches says how many of them ran.
    """
    entry = merged.entry
    own = [branch for branch in entry.branches if branch.source == line]
    untaken = [branch for branch in own if branch.taken == 0]
    found: list[tuple[str, str]] = []
    if untaken and merged.measurer.branches == TARGETS and len(untaken) == len(own):
        found.append((f"{line}{ARROW}anywhere", ANYWHERE.format(line=line)))
    else:
        found.extend(_jump(branch, merged.measurer.branches) for branch in untaken)
    for at, total, ran in entry.branch_counts:
        if at == line and ran < total:
            found.append(
                (f"{ran} of {total}", f"{ran} of line {line}'s {total} branches ran")
            )
    return found


def _jump(branch: Branch, kind: str) -> tuple[str, str]:
    """An untaken branch's short and long annotation."""
    line = branch.source
    if kind == TARGETS and branch.target is not None:
        target = "exit" if branch.target < 0 else str(branch.target)
        destination = (
            "the function exit" if branch.target < 0 else f"line {branch.target}"
        )
        fallback = f"line {line} didn't jump to {destination}"
        return f"{line}{ARROW}{target}", _sentence(branch.why) or fallback
    tag = " (throw)" if branch.why == THROW else ""
    return (
        f"branch {branch.index}{tag}",
        _sentence(branch.why) or f"branch {branch.index}{tag} of line {line} never ran",
    )


def _sentence(why: str) -> str:
    """*why* when it is a sentence; empty for a tag."""
    return "" if why in (WAIVED, THROW) else why


def _has_branches(drawn: Drawn) -> bool:
    entry = drawn.merged.entry
    return drawn.merged.measurer.branches != NO_BRANCHES or bool(
        entry.branch_counts or entry.ranges
    )


def unrun_marks(entry: Entry, text: str) -> dict[int, list[tuple[int, int]]]:
    """The characters of each line that ran whose code never did, by the ranges.

    Per line of *text*, each stretch from its first character's index
    to the index after its last. A range's columns count bytes, so a
    line with characters beyond ASCII has its stretches moved to the
    characters those bytes belong to. A line that never ran is drawn
    missing whole and has none.
    """
    lines = source_lines(text)
    executed = entry.executed
    found: dict[int, list[tuple[int, int]]] = {}
    for line, spans in unrun_columns(entry).items():
        if line not in executed or line > len(lines):
            continue
        starts: list[int] = []
        width = 0
        for char in lines[line - 1]:
            starts.append(width)
            width += len(char.encode("utf-8"))
        marked = [
            (
                bisect.bisect_left(starts, start - 1),
                bisect.bisect_left(starts, width if end is None else end - 1),
            )
            for start, end in spans
        ]
        kept = [(start, end) for start, end in marked if start < end]
        if kept:
            found[line] = kept
    return found


def highlighted(
    name: str, text: str, marks: Mapping[int, Sequence[tuple[int, int]]] | None = None
) -> list[str]:
    """*text* as HTML, line by line, highlighted by Pygments' lexer for *name*.

    A name no lexer claims is drawn as plain text. Each line is HTML
    without its line ending; a token over several lines is split at
    each, and a CRLF or a lone CR ends a line as LF does
    ([livery.extensions.coverage._render.source_lines][]). *marks*
    names, by line, the stretches of characters that never ran, as
    `unrun_marks` gives them: each piece of a token inside one is drawn
    with the class ``nr`` beside the token's own.
    """
    from pygments.lexers import get_lexer_for_filename
    from pygments.lexers.special import TextLexer
    from pygments.util import ClassNotFound

    text = _newlines(text)
    try:
        lexer = get_lexer_for_filename(name, text, stripnl=False, ensurenl=False)
    except ClassNotFound:
        lexer = TextLexer(stripnl=False, ensurenl=False)
    lines: list[list[str]] = [[]]
    offset = 0
    for kind, value in lexer.get_tokens(text):
        css = _token_class(kind)
        for index, piece in enumerate(value.split("\n")):
            if index:
                lines.append([])
                offset = 0
            if not piece:
                continue
            marked = (marks or {}).get(len(lines), ())
            for start, end, unrun in _pieces(offset, len(piece), marked):
                escaped = html.escape(piece[start - offset : end - offset], quote=False)
                classes = f"{css} nr".strip() if unrun else css
                lines[-1].append(
                    f'<span class="{classes}">{escaped}</span>' if classes else escaped
                )
            offset += len(piece)
    drawn = ["".join(line) for line in lines]
    count = len(source_lines(text))
    return (drawn + [""] * count)[:count]


def _pieces(
    start: int, length: int, marked: Sequence[tuple[int, int]]
) -> list[tuple[int, int, bool]]:
    """The characters from *start* on, cut where a stretch of *marked* starts or ends.

    Each piece is its first index, the index after its last, and
    whether a marked stretch holds it; an unmarked token is one piece.
    """
    end = start + length
    cuts = sorted(
        {start, end} | {edge for span in marked for edge in span if start < edge < end}
    )
    found: list[tuple[int, int, bool]] = []
    for first, last in itertools.pairwise(cuts):
        unrun = any(low <= first and last <= high for low, high in marked)
        if found and found[-1][2] == unrun:
            found[-1] = (found[-1][0], last, unrun)
        else:
            found.append((first, last, unrun))
    return found


#: Pygments' short class name for each token kind met, Pygments' own
#: names first.
_TOKEN_CLASSES: dict[object, str] = {}


def _token_class(kind: object) -> str:
    """Pygments' short class name for a token kind: its nearest named parent's."""
    if not _TOKEN_CLASSES:
        from pygments.token import STANDARD_TYPES

        for known, short in STANDARD_TYPES.items():
            _TOKEN_CLASSES[known] = short
    walked = kind
    while walked is not None and walked not in _TOKEN_CLASSES:
        walked = getattr(walked, "parent", None)
    found = _TOKEN_CLASSES.get(walked, "") if walked is not None else ""
    _TOKEN_CLASSES[kind] = found
    return found


def source_lines(text: str) -> list[str]:
    """*text*'s lines, numbered as coverage.py numbers them.

    coverage.py reads a source's CRLF and lone CR line endings as LF,
    so a file checked out with CRLF on Windows has the lines it has
    elsewhere, and none of them ends in a carriage return.
    """
    lines = _newlines(text).split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def _newlines(text: str) -> str:
    """*text* with each CRLF and each lone CR as LF."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _copy_asset(into: Path, name: str) -> str:
    """Copy the shipped asset *name* into *into*; its address, with its digest."""
    content = (ASSETS / name).read_bytes()
    (into / name).write_bytes(content)
    version = hashlib.sha1(content, usedforsecurity=False).hexdigest()[:8]
    return f"{name}?v={version}"


@dataclass(frozen=True)
class _Row:
    """One row of an index: a file, or a region of one."""

    name: str
    url: str
    region: str
    sort: str
    counted: Numbers


def _e(text: str) -> str:
    return html.escape(text, quote=True)


class _Pages:
    """The markup of one package's pages."""

    def __init__(
        self,
        report: Report,
        assets: dict[str, str],
        branched: bool,
        kinds: Sequence[tuple[str, str]],
    ) -> None:
        self.report = report
        self.assets = assets
        self.branched = branched
        self.kinds = kinds

    def percent(self, value: float) -> str:
        return f"{shown(value, self.report.precision)}%"

    def document(
        self, title: str, kind: str, body: str, *, extra: Iterable[tuple[str, str]] = ()
    ) -> str:
        attributes = "".join(f' data-{key}="{_e(value)}"' for key, value in extra)
        return (
            "<!DOCTYPE html>\n"
            '<html lang="en">\n'
            "<head>\n"
            '<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f"<title>{_e(title)}</title>\n"
            f'<link rel="stylesheet" href="{_e(self.assets[STYLE])}">\n'
            f'<script src="{_e(self.assets[SCRIPT])}" defer></script>\n'
            "</head>\n"
            f'<body class="{kind}" data-precision="{self.report.precision}"'
            f"{attributes}>\n{body}</body>\n</html>\n"
        )

    def help(self, keys: Sequence[tuple[str, str]]) -> str:
        items = "".join(
            f"<li><kbd>{_e(key)}</kbd> {_e(what)}</li>" for key, what in keys
        )
        return (
            '<button type="button" class="help-toggle" data-key="?"'
            ' aria-expanded="false" aria-controls="help"'
            ' title="Keyboard shortcuts">?</button>\n'
            f'<section id="help" class="help" hidden><h2>Keys</h2><ul>{items}</ul>'
            "</section>\n"
        )

    # --- the indexes --------------------------------------------------------

    def index(
        self, noun: str, rows: Sequence[_Row], totals: Numbers, last: str, first: str
    ) -> str:
        report = self.report
        title = f"Coverage for {report.title}: {self.percent(totals.percent)}"
        views = ['<nav class="views" aria-label="Indexes">']
        for kind, label, page in (
            ("file", "Files", "index.html"),
            *((k, plural.title(), f"{k}_index.html") for k, plural in self.kinds),
        ):
            if kind == noun:
                views.append(f'<a class="view current" aria-current="page">{label}</a>')
            else:
                views.append(f'<a class="view" href="{page}">{label}</a>')
        views.append("</nav>")
        notes = "".join(f"<li>{_e(note)}</li>" for note in report.notes)
        keys = [
            ("f", "sort by file"),
            *([("n", f"sort by {noun}")] if noun != "file" else []),
            ("s", "sort by statements"),
            ("m", "sort by missing"),
            ("x", "sort by excluded"),
            *(
                [("b", "sort by branches"), ("p", "sort by partial")]
                if self.branched
                else []
            ),
            ("c", "sort by coverage"),
            ("[ ]", "the previous or next file"),
            ("?", "show or hide this help"),
        ]
        body = [
            '<header class="top">\n',
            f'<h1>Coverage for {_e(report.title)}: <span class="figure">'
            f"{self.percent(totals.percent)}</span></h1>\n",
            f'<p class="floor">{_e(report.floor)}</p>\n' if report.floor else "",
            f'<ul class="about">{notes}</ul>\n' if notes else "",
            "\n".join(views),
            "\n",
            '<form class="filters" role="search">'
            '<input id="filter" type="search" placeholder="filter..."'
            ' aria-label="Show the rows whose name holds this text">'
            '<label><input id="hide-covered" type="checkbox"> hide covered</label>'
            "</form>\n",
            self.help(keys),
            "</header>\n",
            "<main>\n",
            self.table(noun, rows, totals),
            '<p class="no-rows" hidden>'
            "No items found using the specified filter.</p>\n",
            self.absent() if noun == "file" else "",
            "</main>\n",
        ]
        return self.document(
            title,
            "index-page",
            "".join(body),
            extra=(("noun", noun), ("prev", last), ("next", first)),
        )

    def absent(self) -> str:
        if not self.report.absent:
            return ""
        items = "".join(
            f"<li><code>{_e(name)}</code></li>" for name in self.report.absent
        )
        return (
            '<section class="absent"><h2>Measured, and no longer in the checkout</h2>'
            "<p>The measured data names these files, which the checkout does not"
            f" have; the next measurement leaves them out.</p><ul>{items}</ul>"
            "</section>\n"
        )

    def table(self, noun: str, rows: Sequence[_Row], totals: Numbers) -> str:
        region = noun != "file"
        lead = 2 if region else 1
        heads = ["<thead>\n"]
        if self.branched:
            heads.append(
                '<tr class="groups">'
                f'<th colspan="{lead}"></th>'
                '<th colspan="4">Statements</th>'
                '<th colspan="3">Branches</th>'
                "<th>Total</th></tr>\n"
            )
        columns = [("file", "File", "f", "text")]
        if region:
            columns.append(("region", noun, "n", "text"))
        if self.branched:
            columns.append(("statements-coverage", "coverage", "", "number"))
        columns += [
            ("statements", "statements", "s", "number"),
            ("missing", "missing", "m", "number"),
            ("excluded", "excluded", "x", "number"),
        ]
        if self.branched:
            columns += [
                ("branches-coverage", "coverage", "", "number"),
                ("branches", "branches", "b", "number"),
                ("partial", "partial", "p", "number"),
            ]
        columns.append(("coverage", "coverage", "c", "number"))
        cells = "".join(
            f'<th data-column="{key}" data-type="{kind}"'
            + (f' data-key="{shortcut}"' if shortcut else "")
            + f' aria-sort="none"><button type="button">{_e(label)}</button></th>'
            for key, label, shortcut, kind in columns
        )
        heads.append(f'<tr class="columns">{cells}</tr>\n</thead>\n')
        body = ["<tbody>\n"]
        for row in rows:
            body.append(self.row(row, region))
        body.append("</tbody>\n")
        foot = (
            '<tfoot>\n<tr class="total"><td class="name">Total</td>'
            + ('<td class="name"></td>' if region else "")
            + self.cells(totals, total=True)
            + "</tr>\n</tfoot>\n"
        )
        return f'<table class="index">\n{"".join(heads)}{"".join(body)}{foot}</table>\n'

    def row(self, row: _Row, region: bool) -> str:
        counted = row.counted
        data = (
            f' data-statements="{counted.statements}"'
            f' data-executed="{counted.executed}"'
            f' data-excluded="{counted.excluded}"'
            f' data-branches="{counted.branches}"'
            f' data-taken="{counted.taken}"'
            f' data-partial="{counted.partial}"'
            f' data-covered="{counted.covered}"'
            f' data-coverable="{counted.coverable}"'
        )
        cells = [
            f'<td class="name" data-sort="{_e(row.name)}">'
            f'<a href="{_e(row.url)}">{_e(row.name)}</a></td>'
        ]
        if region:
            css = "name region" if row.sort else "name region outside"
            cells.append(
                f'<td class="{css}" data-sort="{_e(row.sort)}">'
                f'<a href="{_e(row.url)}">{_e(row.region)}</a></td>'
            )
        return f"<tr{data}>{''.join(cells)}{self.cells(counted)}</tr>\n"

    def cells(self, counted: Numbers, *, total: bool = False) -> str:
        def ratio(part: int, whole: int, value: float, key: str) -> str:
            mark = f' data-total="{key}"' if total else ""
            return (
                f'<td class="pc" data-sort="{value:.6f}" data-ratio="{part} {whole}"'
                f"{mark}>{self.percent(value)}</td>"
            )

        def count(value: int, key: str) -> str:
            mark = f' data-total="{key}"' if total else ""
            return f'<td data-sort="{value}"{mark}>{value}</td>'

        found: list[str] = []
        if self.branched:
            found.append(
                ratio(
                    counted.executed,
                    counted.statements,
                    counted.statements_percent,
                    "statements-coverage",
                )
            )
        found += [
            count(counted.statements, "statements"),
            count(counted.missing, "missing"),
            count(counted.excluded, "excluded"),
        ]
        if self.branched:
            found += [
                ratio(
                    counted.taken,
                    counted.branches,
                    counted.branches_percent,
                    "branches-coverage",
                ),
                count(counted.branches, "branches"),
                count(counted.partial, "partial"),
            ]
        found.append(
            ratio(counted.covered, counted.coverable, counted.percent, "coverage")
        )
        return "".join(found)

    # --- a file's page ------------------------------------------------------

    def file(self, drawn: Drawn, counted: Numbers, before: str, after: str) -> str:
        merged = drawn.merged
        title = f"Coverage for {drawn.name}: {self.percent(counted.percent)}"
        branched = _has_branches(drawn)
        toggles = [
            '<nav class="toggles" aria-label="Lines highlighted">',
            f'<span class="statements">{counted.statements} statements</span>',
            self.toggle("run", "r", f"{counted.executed} run"),
            self.toggle("mis", "m", f"{counted.missing} missing"),
            self.toggle("exc", "x", f"{counted.excluded} excluded"),
        ]
        if branched:
            toggles.append(self.toggle("par", "p", f"{counted.partial} partial"))
        toggles.append("</nav>")
        suites = [unit for unit in merged.units if unit != "."]
        about = f"Measured by {merged.measurer.label}" + (
            f" while the suites of {', '.join(suites)} ran." if suites else "."
        )
        keys = [
            ("r m x p", "show or hide the lines of a kind"),
            ("j k", "the next or previous highlighted chunk"),
            ("0", "the top of the page"),
            ("1", "the first highlighted chunk"),
            ("[ ]", "the previous or next file"),
            ("u", "the index"),
            ("?", "show or hide this help"),
        ]
        header = (
            '<header class="top">\n'
            f'<h1>Coverage for <code>{_e(drawn.name)}</code>: <span class="figure">'
            f"{self.percent(counted.percent)}</span></h1>\n"
            f'<p class="about">{_e(about)}</p>\n' + "\n".join(toggles) + "\n"
            '<nav class="pager" aria-label="Pages">'
            f'<a href="{_e(before)}" rel="prev" data-key="[">&#xab; prev</a>'
            '<a href="index.html" data-key="u">&#x2191; index</a>'
            f'<a href="{_e(after)}" rel="next" data-key="]">&#xbb; next</a>'
            "</nav>\n" + self.help(keys) + "</header>\n"
        )
        if drawn.text is None:
            measured = merged.entry.digest[:12] or "content that was not read"
            body = (
                '<main class="moved">\n<p class="notice">This file was measured at'
                " other content: the measurement is of blob"
                f" <code>{_e(measured)}</code>, and the checkout holds blob"
                f" <code>{_e(drawn.digest[:12])}</code>. Its lines are not drawn;"
                " its numbers are the measurement's.</p>\n"
                "</main>\n"
            )
            return self.document(title, "file-page", header + body)
        # The run lines start unhighlighted, as the run toggle starts
        # off, so a page read without its script shows the same.
        return self.document(
            title, "file-page hide-run", header + self.source(drawn, branched)
        )

    def toggle(self, category: str, key: str, label: str) -> str:
        pressed = "false" if category == "run" else "true"
        return (
            f'<button type="button" class="toggle {category}"'
            f' data-category="{category}" data-key="{key}"'
            f' aria-pressed="{pressed}">{_e(label)}</button>'
        )

    def source(self, drawn: Drawn, branched: bool) -> str:
        merged = drawn.merged
        entry = merged.entry
        text = drawn.text or ""
        marks = unrun_marks(entry, text) if entry.ranges else {}
        drawn_lines = highlighted(drawn.name, text, marks)
        categories = line_categories(entry, len(drawn_lines))
        counting = merged.measurer.counts != "presence"
        names, codes = self.contexts(entry)
        lines = ['<main class="source">\n']
        for number, (code, category) in enumerate(
            zip(drawn_lines, categories, strict=True), start=1
        ):
            classes = f"ln {category}" if category else "ln"
            parts = [
                f'<div class="{classes}" id="t{number}">',
                f'<a class="no" href="#t{number}">{number}</a>',
                f'<span class="src">{code}</span>',
            ]
            runs = entry.counts.get(number, 0)
            if counting and runs and number in entry.statements:
                parts.append(
                    f'<span class="runs" title="ran {runs} times">{runs}&#xd7;</span>'
                )
            if category == "par" and branched:
                annotated = "".join(
                    f'<span class="jump" tabindex="0">{_e(short)}'
                    f'<span class="why" role="tooltip">{_e(long)}</span></span>'
                    for short, long in jumps(merged, number)
                )
                parts.append(f'<span class="jumps">{annotated}</span>')
            held = entry.reached.get(number)
            if category in ("exc", "mis", "par", "run") and held:
                parts.append(self.context_label(held, codes))
            parts.append("</div>\n")
            lines.append("".join(parts))
        lines.append("</main>\n")
        if names:
            listed = json.dumps(names).replace("<", "\\u003c").replace(">", "\\u003e")
            lines.append(
                f'<script type="application/json" id="contexts">{listed}</script>\n'
            )
        return "".join(lines)

    def contexts(self, entry: Entry) -> tuple[list[str], dict[int, int]]:
        """The page's contexts, sorted as people read them, and each one's place."""
        used = sorted(
            {index for held in entry.reached.values() for index in held},
            key=lambda index: _human(self.label_of(index)),
        )
        return [self.label_of(index) for index in used], {
            index: place for place, index in enumerate(used)
        }

    def label_of(self, index: int) -> str:
        name = self.report.contexts[index]
        return name or EMPTY_CONTEXT

    def context_label(self, held: frozenset[int], codes: dict[int, int]) -> str:
        labels = {self.label_of(index) for index in held}
        if labels == {EMPTY_CONTEXT}:
            return f'<span class="ctx">{EMPTY_CONTEXT}</span>'
        places = " ".join(
            str(place) for place in sorted(codes[index] for index in held)
        )
        return (
            f'<button type="button" class="ctx" data-contexts="{places}"'
            f' aria-expanded="false">{len(held)} ctx</button>'
        )


def _human(text: str) -> tuple[list[str | int], str]:
    """A key that sorts *text* as people read it: its numbers by value."""
    pieces: list[str | int] = [
        int(piece) if piece.isdigit() else piece for piece in re.split(r"(\d+)", text)
    ]
    return pieces, text
