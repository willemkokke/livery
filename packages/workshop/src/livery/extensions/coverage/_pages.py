"""Draw the site's coverage pages from the measured data, with coverage.py.

The docs extension asks its ``docs.coverage-pages`` query for the
function that draws the coverage pages. This extension answers in its
``[for.docs]`` table, so the answer is asked only while docs is listed:
[livery.extensions.coverage._pages.renderer][] answers with
[livery.extensions.coverage._pages.render_coverage_pages][], which runs
coverage.py's ``coverage html`` for each package. In the merge point's
gate job the pages read main's coverage record, through
[livery.workshop._coverage_store][]; anywhere else they read the local
``.coverage`` a gate run left.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import livery.footman as footman
import livery.toolroom.tools as tools
from livery import git
from livery.footman import fail

if TYPE_CHECKING:
    from collections.abc import Callable

    from livery.workshop._packages import Package


def renderer(root: Path) -> Callable[[Path, tuple[Package, ...]], list[str]]:
    """Answer ``docs.coverage-pages`` with the function that draws the pages.

    The docs extension asks with the workspace root, which the answer
    does not depend on, and calls the answer with the root and the
    packages that declare an ``htmlcov`` report.
    """
    del root
    return render_coverage_pages


def stored_union(
    root: Path, labels: list[str], into: Path
) -> tuple[list[Path], list[str]]:
    """Every unit of main's record on each leg in *labels*, under *into*.

    Returns the files written and the misses, named.

    The site's coverage pages read this in the merge point's deploy
    job: the union main's gate judged, whatever its legs ran. A leg
    whose record cannot be read, or holds nothing, is a named miss,
    never a refusal: the pages render what there is.
    """
    from livery.workshop._coverage_store import MAIN, recorded

    written: list[Path] = []
    misses: list[str] = []
    for label in labels:
        held = recorded(root, leg=label)
        if held.failed:
            misses.append(f"{MAIN}/{label} ({held.reason})")
            continue
        if not held.units:
            misses.append(f"{MAIN}/{label}: nothing recorded")
            continue
        for path, unit in sorted(held.units.items()):
            written.append(
                _write_unit(
                    into / label, f"reuse-{git.slug(path)}.coverage", unit.files
                )
            )
    return written, misses


def render_coverage_pages(root: Path, packages: tuple[Package, ...]) -> list[str]:
    """Render an htmlcov tree for each of *packages* from the measured data.

    The docs extension hands over the packages that declare an
    ``htmlcov`` report, and each gets its tree rendered by ``coverage
    html`` from the workspace's measured data, scoped to its own files.
    In the merge point's gate job the data is main's coverage record,
    every unit on every check leg, the union main's gate judged
    whatever its legs ran; anywhere else it is the local ``.coverage``
    a gate run left. With none, nothing renders and the coverage page
    states the absence. Returns the members rendered.
    """
    from livery.workshop._ci_context import run_context

    # The coverage CLI on named files: ambient COVERAGE_* variables
    # from a metered shell would re-point it at that process's live
    # data file, and the page would find no union.
    unmetered = _unmetered()
    legs, misses = _stored_legs(root)
    for miss in misses:
        print(f"  coverage: {miss}: not in the record; the pages render without it")
    if legs:
        print(f"  coverage: the pages read {len(legs)} recorded unit file(s)")
    if legs:
        with tempfile.TemporaryDirectory() as scratch:
            copies = []
            for index, leg in enumerate(legs):
                copy = Path(scratch) / f".coverage.{index}"
                shutil.copy2(leg, copy)
                copies.append(str(copy))
            combined = tools.coverage.opts(
                cwd=root, env=unmetered, nofail=True, recorded=False
            )("combine", "--keep", *copies)
            if combined.code != 0:
                fail(
                    f"coverage combine exited {combined.code}:\n"
                    f"{combined.stdout}{combined.stderr}"
                )
    if not (root / ".coverage").is_file():
        return []
    rendered: list[str] = []
    for package in packages:
        name = package.member
        result = tools.coverage.opts(
            cwd=root, env=unmetered, nofail=True, recorded=False
        )(
            "html",
            f"--include=packages/{name}/*",
            "-d",
            f"packages/{name}/htmlcov",
        )
        output = result.stdout + result.stderr
        if result.code != 0 and "No data to report" in output:
            # A package the data never touched, a unit the store could
            # not supply, say: its page states the absence.
            print(f"  coverage: {name}: no measured data; its page states the absence")
            continue
        if (
            result.code != 0
            and "No source for code" in output
            and run_context() is None
        ):
            # A desk's own data outlives a file move until the next gate
            # run measures again; a report from it would show old lines
            # against new files, so the page states the absence and an
            # older report goes. Inside CI the data is the record of the
            # tree being built, and a missing source there stays red.
            shutil.rmtree(root / "packages" / name / "htmlcov", ignore_errors=True)
            print(
                f"  coverage: {name}: the local data names files that moved or"
                f" went; its page states the absence until `{footman.prog()} check`"
                " measures again"
            )
            continue
        if result.code != 0:
            fail(
                f"coverage html for {name} exited {result.code}:\n"
                f"{result.stdout}{result.stderr}"
            )
        rendered.append(name)
    return rendered


def _stored_legs(root: Path) -> tuple[list[Path], list[str]]:
    """In the merge point's gate job, main's recorded units for every check leg.

    Returns the files and the misses. The gate job renders the trees
    after its union wrote the record, for the site it publishes.
    Anywhere else nothing is pulled: a pull request's docs job builds
    without the legs' data by design, and a machine's build renders the
    local data a gate run left, or states the absence.
    """
    from livery.workshop._ci_context import (
        LEG_VARIABLE,
        POINT_VARIABLE,
        run_context,
        state_store,
    )
    from livery.workshop._points import check_legs

    deploying = (
        os.environ.get(POINT_VARIABLE) == "merge"
        and os.environ.get(LEG_VARIABLE) == "gate"
    )
    if run_context() is None or not deploying:
        return [], []
    from livery.git import remote_snapshot

    with remote_snapshot(state_store(root), fetch=("coverage/main/",)):
        return stored_union(root, check_legs(root), root / "coverage-data")


def _unmetered() -> dict[str, str]:
    """The environment for a coverage CLI call on a named data file.

    Ambient ``COVERAGE_*`` and ``COV_CORE_*`` variables, from a shell
    that armed the meter or a pytest-cov parent, would re-point the
    coverage CLI at that process's live data file and meter the CLI
    process itself; scrubbing them keeps the call on the files the
    caller named.
    """
    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("COVERAGE_", "COV_CORE_"))
    }


def _write_unit(
    folder: Path, name: str, files: dict[str, list[tuple[int, int]]]
) -> Path:
    """One unit's arcs as a coverage data file under *folder*; the path."""
    from coverage import CoverageData

    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    data = CoverageData(basename=str(path))
    data.add_arcs(
        {file: {(arc[0], arc[1]) for arc in arcs} for file, arcs in files.items()}
    )
    data.write()
    return path
