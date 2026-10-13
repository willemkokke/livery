"""Coverage as lines per file: the shape every native measurer reduces to.

A native kind's tests run instrumented, and the measurer beside the
compiler that built them reads which lines of each source file ran.
Whatever tool answered, the answer here is one shape: per file, line
number to hit count, every instrumentable line present and an
unexecuted one at zero. Python keeps coverage.py's arcs; this module
is for every kind that measures lines, and
[livery.extensions.coverage._lines.measures_lines][] tells the two
apart.

A native test step leaves one part per package at the workspace root,
beside coverage.py's own parts: the cmake extension's test, for a CMake
build, writes it in the shape
[livery.extensions.coverage._lines.write_part][] writes, which is the
contract between the two, and the leg and the union read it
([livery.extensions.coverage._lines.read_parts][]). The union across
legs is [livery.extensions.coverage._lines.merge][], a set union of
lines with the hits added, and a package's number is
[livery.extensions.coverage._lines.percent][] over its source files.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from livery import git
from livery.footman import fail

if TYPE_CHECKING:
    from livery.workshop._packages import Package

#: File to line number to hit count; every instrumentable line is a key.
Lines = dict[str, dict[int, int]]

#: The parts a run leaves at the workspace root, one per package,
#: beside coverage.py's ``.coverage.*`` parts; the ignore lines this
#: extension ships cover both.
PART_PREFIX = ".coverage-lines."


def measures_lines(package: Package) -> bool:
    """Whether *package*'s suite is measured as lines: a native kind's, no python one's.

    The workspace's own tests carry no kind and are python's.
    """
    from livery.workshop._kinds import is_python_kind, kind_names

    return package.kind in kind_names() and not is_python_kind(package.kind)


def part_path(root: Path, package_path: str) -> Path:
    """Where *package_path*'s lines part lives under *root*."""
    return root / f"{PART_PREFIX}{git.slug(package_path)}.json"


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
