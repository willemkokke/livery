"""Coverage as lines per file: the shape every native measurer reduces to.

A native kind's tests run instrumented, and the measurer beside the
compiler that built them reads which lines of each source file ran.
Whatever tool answered, gcov's JSON, llvm-cov's lcov or a Cobertura
report, the answer here is one shape: per file, line number to hit
count, every instrumentable line present and an unexecuted one at
zero. Python keeps coverage.py's arcs; this module is for every kind
that measures lines.

A run leaves one part per package at the workspace root, beside
coverage.py's own parts, and the leg and the union read them there:
[livery.workshop._coverage_lines.write_part][] after the tests,
[livery.workshop._coverage_lines.read_parts][] before the floors. The
union across legs is [livery.workshop._coverage_lines.merge][], a set
union of lines with the hits added, and a package's number is
[livery.workshop._coverage_lines.percent][] over its source files.
"""

from __future__ import annotations

import json
import re
from pathlib import Path, PureWindowsPath
from typing import TYPE_CHECKING, Any
from xml.etree import ElementTree

from livery.footman import fail
from livery.workshop._state import slug

if TYPE_CHECKING:
    from livery.workshop._packages import Package

#: File to line number to hit count; every instrumentable line is a key.
Lines = dict[str, dict[int, int]]

#: The parts a run leaves at the workspace root, one per package,
#: beside coverage.py's ``.coverage.*`` parts and under the same
#: ignore rule.
PART_PREFIX = ".coverage-lines."

#: The measurer a compiler family's build takes, by CMake's compiler id.
FAMILIES = {
    "GNU": "gcov",
    "Clang": "llvm",
    "AppleClang": "llvm",
    "MSVC": "msvc",
}


def measurer_for(compiler_id: str) -> str:
    """The measurer for *compiler_id* as CMake spells it; empty without one."""
    return FAMILIES.get(compiler_id, "")


def part_path(root: Path, package_path: str) -> Path:
    """Where *package_path*'s lines part lives under *root*."""
    return root / f"{PART_PREFIX}{slug(package_path)}.json"


def write_part(root: Path, package_path: str, lines: Lines) -> Path:
    """Write *lines* as *package_path*'s part; the path written."""
    path = part_path(root, package_path)
    path.write_text(
        json.dumps({"package": package_path, "files": pairs(lines)}, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    return path


def read_parts(root: Path) -> dict[str, Lines]:
    """Every lines part under *root*, by package; one that is not a part refuses."""
    found: dict[str, Lines] = {}
    for path in sorted(root.glob(f"{PART_PREFIX}*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            fail(f"{path.name}: not a lines part ({error})")
        if not isinstance(raw, dict) or not isinstance(raw.get("files"), dict):
            fail(f"{path.name}: not a lines part (no files table)")
        package_path = str(raw.get("package", ""))
        if not package_path:
            fail(f"{path.name}: not a lines part (names no package)")
        found[package_path] = from_pairs(raw["files"])
    return found


def remove_parts(root: Path) -> None:
    """Delete every lines part under *root*, before a run leaves fresh ones."""
    for path in root.glob(f"{PART_PREFIX}*.json"):
        path.unlink()


def pairs(lines: Lines) -> dict[str, list[tuple[int, int]]]:
    """*lines* as sorted ``(line, hits)`` pairs per file: the unit row's shape."""
    return {
        name: sorted((line, hits) for line, hits in counts.items())
        for name, counts in sorted(lines.items())
    }


def from_pairs(files: Any) -> Lines:
    """*files*, ``(line, hits)`` pairs per file as a row carries them, as lines."""
    lines: Lines = {}
    if not isinstance(files, dict):
        return lines
    for name, entries in files.items():
        if not isinstance(entries, list):
            continue
        counts: dict[int, int] = {}
        for entry in entries:
            if isinstance(entry, list | tuple) and len(entry) == 2:
                counts[int(entry[0])] = int(entry[1])
        lines[str(name)] = counts
    return lines


def merge(*many: Lines) -> Lines:
    """The union of *many*: every line any of them knows, its hits added up."""
    merged: Lines = {}
    for lines in many:
        for name, counts in lines.items():
            into = merged.setdefault(name, {})
            for line, hits in counts.items():
                into[line] = into.get(line, 0) + hits
    return merged


def relativise(lines: Lines, root: Path) -> Lines:
    """*lines* keyed by workspace-relative posix paths; files outside *root* dropped.

    A measurer names files the way the compiler saw them, absolute and
    with the host's separators; the row and the floors want the path
    the repository knows.
    """
    base = root.resolve()
    kept: Lines = {}
    for name, counts in lines.items():
        candidate = Path(PureWindowsPath(name).as_posix() if "\\" in name else name)
        if not candidate.is_absolute():
            candidate = base / candidate
        try:
            relative = candidate.resolve().relative_to(base).as_posix()
        except ValueError:
            continue
        kept[relative] = dict(counts)
    return kept


def within(lines: Lines, package: Package) -> Lines:
    """The files of *lines* under *package*, keyed as they are."""
    prefix = f"{package.path}/"
    return {name: counts for name, counts in lines.items() if name.startswith(prefix)}


def percent(lines: Lines, package: Package) -> float | None:
    """*package*'s line coverage over its source files; None when it has none measured.

    A file counts when its category in the package is ``source``, so
    a test's own lines and a build script never dilute the number.
    The percentage is the lines hit over every instrumentable line;
    a package whose sources have no instrumentable line is 100.
    """
    from livery.workshop._categories import category_of

    prefix = f"{package.path}/"
    executed = instrumentable = 0
    measured = False
    for name, counts in lines.items():
        if not name.startswith(prefix):
            continue
        if category_of(package, name[len(prefix) :]).name != "source":
            continue
        measured = True
        instrumentable += len(counts)
        executed += sum(1 for hits in counts.values() if hits > 0)
    if not measured:
        return None
    return 100.0 * executed / instrumentable if instrumentable else 100.0


def from_gcov_json(text: str) -> Lines:
    """The lines gcov's ``--json-format`` output names; a non-document refuses."""
    lines: Lines = {}
    for document in _json_documents(text):
        files = document.get("files") if isinstance(document, dict) else None
        if not isinstance(files, list):
            fail("gcov printed no files table; its JSON format has one")
        for entry in files:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("file", ""))
            rows = entry.get("lines")
            if not name or not isinstance(rows, list):
                continue
            counts = lines.setdefault(name, {})
            for row in rows:
                if isinstance(row, dict) and "line_number" in row:
                    line = int(row["line_number"])
                    counts[line] = counts.get(line, 0) + int(row.get("count", 0))
    return lines


def _json_documents(text: str) -> list[Any]:
    """Every JSON document in *text*, one per line or one whole; non-JSON refuses."""
    stripped = text.strip()
    if not stripped:
        return []
    try:
        return [json.loads(stripped)]
    except ValueError:
        pass
    documents: list[Any] = []
    for line in stripped.splitlines():
        if not line.strip():
            continue
        try:
            documents.append(json.loads(line))
        except ValueError:
            fail(f"gcov printed a line that is not JSON: {line[:120]}")
    return documents


def from_lcov(text: str) -> Lines:
    """The lines an lcov trace names: ``SF:`` opens a file, ``DA:`` counts a line."""
    lines: Lines = {}
    current: dict[int, int] | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("SF:"):
            current = lines.setdefault(line[3:], {})
        elif line.startswith("DA:") and current is not None:
            number, _, hits = line[3:].partition(",")
            count = hits.split(",")[0]
            current[int(number)] = current.get(int(number), 0) + int(count or 0)
        elif line == "end_of_record":
            current = None
    return lines


def from_cobertura(text: str) -> Lines:
    """The lines a Cobertura report names; a report that is not XML refuses.

    A class's ``filename`` is taken as it is when absolute, else under
    the report's first ``<source>``.
    """
    try:
        document = ElementTree.fromstring(text)
    except ElementTree.ParseError as error:
        fail(f"the coverage report is not XML ({error})")
    sources = [
        (node.text or "").strip() for node in document.iter("source") if node.text
    ]
    lines: Lines = {}
    for klass in document.iter("class"):
        name = klass.get("filename", "")
        if not name:
            continue
        if not _absolute(name) and sources:
            name = (
                sources[0].rstrip("\\/") + ("\\" if "\\" in sources[0] else "/") + name
            )
        counts = lines.setdefault(name, {})
        for row in klass.iter("line"):
            number = row.get("number")
            if number is None:
                continue
            counts[int(number)] = counts.get(int(number), 0) + int(row.get("hits", "0"))
    return lines


def _absolute(name: str) -> bool:
    return Path(name).is_absolute() or re.match(r"^[A-Za-z]:[\\/]", name) is not None
