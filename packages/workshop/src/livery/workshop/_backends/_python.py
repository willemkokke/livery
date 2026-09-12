"""The Python backend: the quality verbs for ``type = "python"``.

One invocation covers every Python package at once: the checkers read
their scopes from the workspace's own configuration, so the whole
repository is linted exactly as CI lints it, and a tracked file
outside any package still cannot pass the gate and fail the build.
The affected engine narrows the same verbs to a package subset by
passing explicit paths; ty and pyrefly always check their configured
whole, because their runs cost seconds and their configs pin the
platform matrix.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import livery.footman as footman
from livery import toolroom
from livery.footman import fail
from livery.toolroom import basedpyright, mypy, pyrefly, pytest, ruff, ruff_format, ty
from livery.workshop._contract import load_contract
from livery.workshop._packages import Package
from livery.workshop._state import RunContext, slug

if TYPE_CHECKING:
    from livery.workshop._coverage_store import Record
    from livery.workshop._git_ops import GitOps

#: The whole repo, as CI lints it.
SRC = (".",)


def package_paths(packages: tuple[Package, ...]) -> tuple[str, ...]:
    """The src and tests directories the *packages* own, as they exist.

    The workspace's own tests, a unit whose directory is the tests
    themselves, contribute that directory.
    """
    from livery.workshop._coverage_store import WORKSPACE_TESTS

    paths = []
    for package in packages:
        if package.path == WORKSPACE_TESTS:
            if package.directory.is_dir():
                paths.append(package.path)
            continue
        for name in ("src", "tests"):
            directory = package.directory / name
            if directory.is_dir():
                paths.append(f"{package.path}/{name}")
    return tuple(paths)


#: The python suffixes ruff owns; other files pass through untouched
#: when an explicit path names them.
_PY_SUFFIXES = (".py", ".pyi")

#: What ``--safe-fix`` refuses to let ruff remove: rules that delete
#: code an edit in flight has not finished writing. This is the one
#: place the meaning of safe-fix lives for python.
_SAFE_UNFIXABLE = "F401"


def _python_paths(paths: tuple[str, ...]) -> tuple[str, ...]:
    """Keep the directories and python files; drop foreign files.

    A directory is ruff's to walk; a named python file is its to
    read; a named C++ or markdown file is not, so it passes through
    as a no-op rather than an error.
    """
    kept: list[str] = []
    for entry in paths:
        path = Path(entry)
        if not path.is_file() or path.suffix in _PY_SUFFIXES:
            kept.append(entry)
    return tuple(kept)


def run_format(
    check: bool = False, safe_fix: bool = False, paths: tuple[str, ...] = SRC
) -> None:
    """Format with ruff; *check* reports instead of rewriting.

    Formatting removes no code, so ``safe_fix`` rewrites exactly as
    a plain fix does; the flag exists for symmetry with lint.
    """
    chosen = _python_paths(paths)
    if not chosen:
        return
    ruff_format(*chosen, check=check and not safe_fix)


def run_lint(
    fix: bool = False, safe_fix: bool = False, paths: tuple[str, ...] = SRC
) -> None:
    """Lint with ruff; *fix* applies safe fixes, *safe_fix* fewer.

    ``safe_fix`` applies fixes but never the code-removing rules
    (unused imports): an edit in flight adds an import before the
    code that uses it, and removing it between the two edits deletes
    real work. What safe-fix withholds is _SAFE_UNFIXABLE, defined
    here and nowhere above.
    """
    chosen = _python_paths(paths)
    if not chosen:
        return
    if safe_fix:
        ruff.check(*chosen, fix=True, unfixable=_SAFE_UNFIXABLE)
        return
    ruff.check(*chosen, fix=fix)


def run_typecheck(paths: tuple[str, ...] = ()) -> None:
    """Type-check with all four gating checkers, in parallel.

    basedpyright runs with warnings gating as errors. mypy is strict
    on livery.* and checks every test body as consumer code, once per
    platform (linux from config, darwin and win32 by flag), since
    mypy has no all-platforms mode. ty and pyrefly check every
    platform at once at the scopes pyproject pins. All four gate: a
    checker livery uses is a checker the tree is clean against.

    *paths* narrows basedpyright and mypy to the affected subset; ty
    and pyrefly keep their configured whole either way.
    """
    from livery.footman import parallel, step

    def based() -> None:
        basedpyright(*paths, warnings=True)

    # Each mypy run gets its own cache dir: the SQLite cache does not
    # tolerate three concurrent writers on one file.
    def mypy_linux() -> None:
        mypy(*paths, cache_dir=".mypy_cache/linux")

    def mypy_darwin() -> None:
        mypy(*paths, platform="darwin", cache_dir=".mypy_cache/darwin")

    def mypy_win32() -> None:
        mypy(*paths, platform="win32", cache_dir=".mypy_cache/win32")

    def run_ty() -> None:
        ty.check()

    def run_pyrefly() -> None:
        pyrefly("check")

    parallel(
        step(based, title="basedpyright")(),
        step(mypy_linux)(),
        step(mypy_darwin)(),
        step(mypy_win32)(),
        step(run_ty, title="ty")(),
        step(run_pyrefly, title="pyrefly")(),
    )


def run_typecomplete(packages: tuple[Package, ...]) -> None:
    """Verify each package's public API is 100% type-complete.

    The importable module is derived from the distribution name
    (``livery-forge`` is ``livery.forge``); the exit code is the
    verdict, 0 only when every public symbol has a fully known type.
    """
    for package in packages:
        basedpyright(verifytypes=module_for(package), ignoreexternal=True)


def module_for(package: Package) -> str:
    """The package's importable module, read from its src tree.

    The single-directory chain under ``src/`` down to the first
    directory carrying python files is the module
    (``src/livery/forge`` is ``livery.forge``). Deriving it from the
    distribution name guessed wrong the moment a member's own name
    carried a hyphen: ``loop-echo`` under a workspace prefix became
    ``loop.echo``, a module that does not exist. A package without a
    src tree falls back to the dist-name spelling, underscores for
    hyphens beyond the namespace dot.
    """
    src = package.directory / "src"
    if src.is_dir():
        parts: list[str] = []
        node = src
        while True:
            dirs = [d for d in node.iterdir() if d.is_dir() and d.name.isidentifier()]
            has_py = any(f.suffix == ".py" for f in node.iterdir() if f.is_file())
            if parts and (has_py or len(dirs) != 1):
                return ".".join(parts)
            if len(dirs) != 1:
                break
            node = dirs[0]
            parts.append(node.name)
        if parts:
            return ".".join(parts)
    head, _, tail = package.name.partition("-")
    if not tail:
        return head
    return head + "." + tail.replace("-", "_")


def current_version(package: Package) -> str:
    """The version the package's ``pyproject.toml`` declares."""
    data = tomllib.loads((package.directory / "pyproject.toml").read_text("utf-8"))
    return str(data.get("project", {}).get("version", "0.0.0"))


def stamp_version(package: Package) -> _Stamper:
    """Where a Python package's version lives, ready to stamp."""
    return _Stamper(package)


class _Stamper:
    """Stamp a version into a Python package's places, idempotently."""

    def __init__(self, package: Package) -> None:
        self._package = package

    def stamp(self, version: str) -> list[str]:
        """Write *version* into pyproject and ``__version__``; what changed."""
        import re as _re

        changed = []
        pyproject = self._package.directory / "pyproject.toml"
        text = pyproject.read_text("utf-8")
        stamped, count = _re.subn(
            r'^version = "[^"]+"$',
            f'version = "{version}"',
            text,
            count=1,
            flags=_re.M,
        )
        if count != 1:
            fail(f"{pyproject} has no version line to stamp")
        if stamped != text:
            pyproject.write_text(stamped, encoding="utf-8")
            changed.append("pyproject.toml")
        for init in (self._package.directory / "src").rglob("__init__.py"):
            text = init.read_text("utf-8")
            stamped, count = _re.subn(
                r'^__version__ = "[^"]+"$',
                f'__version__ = "{version}"',
                text,
                count=1,
                flags=_re.M,
            )
            if count and stamped != text:
                init.write_text(stamped, encoding="utf-8")
                changed.append(str(init.relative_to(self._package.directory)))
        return changed


@dataclass(frozen=True)
class FloorPolicy:
    """How a package's coverage is judged: a committed floor, or the ratchet.

    Attributes:
        floor: The committed percentage, or ``None`` under auto-ratchet.
        ratchet: Whether the mark on the store is the floor.
        epsilon: The tolerance in percentage points, in both modes.
    """

    floor: float | None
    ratchet: bool
    epsilon: float


def coverage_policy(package: Package) -> FloorPolicy | None:
    """The package's ``[qa] coverage-floor`` policy, or ``None`` when it has none.

    The floor is a percentage, or the mode ``"auto-ratchet"``; anything
    else refuses by name. ``coverage-epsilon`` is a number of
    percentage points, 0.5 when absent; a negative or non-numeric one
    refuses.
    """
    from livery.workshop._coverage_marks import AUTO_RATCHET, DEFAULT_EPSILON

    qa = load_contract(package.directory / "workshop.toml").get("qa") or {}
    raw = qa.get("coverage-floor")
    if raw is None:
        return None
    raw_epsilon = qa.get("coverage-epsilon", DEFAULT_EPSILON)
    if isinstance(raw_epsilon, bool) or not isinstance(raw_epsilon, int | float):
        fail(
            f"{package.path}: [qa] coverage-epsilon is a number of percentage"
            f" points; found {raw_epsilon!r}"
        )
    epsilon = float(raw_epsilon)
    if epsilon < 0:
        fail(f"{package.path}: [qa] coverage-epsilon must not be negative")
    if raw == AUTO_RATCHET:
        return FloorPolicy(None, True, epsilon)
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        fail(
            f"{package.path}: [qa] coverage-floor is a percentage or"
            f' "{AUTO_RATCHET}"; found {raw!r}'
        )
    return FloorPolicy(float(raw), False, epsilon)


def coverage_floor(package: Package) -> float | None:
    """The committed coverage floor from the package's contract, or None.

    ``None`` under auto-ratchet too, whose floor is the mark on the
    store; [livery.workshop._backends._python.coverage_policy][] tells
    the two apart.
    """
    policy = coverage_policy(package)
    return None if policy is None else policy.floor


def measured_coverage(root: Path, packages: tuple[Package, ...]) -> dict[str, float]:
    """Per-package coverage from the run's ``.coverage`` data, statements and branches.

    A package's percentage is the statements and branches of its
    files the data reached, over all of them, the figure coverage.py
    reports as a file's total; a package with none to reach is 100.
    """
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as handle:
        report = handle.name
    # The data read is *root*'s, by contract. Under a metered gate the
    # ambient COVERAGE_*/COV_CORE_* variables re-point the coverage CLI
    # at the outer run's live data file, whose parallel parts are still
    # being written; scrubbing them keeps the read on the file the
    # caller named.
    scrubbed = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("COVERAGE_", "COV_CORE_"))
    }
    result = toolroom.coverage.opts(cwd=root, env=scrubbed)("json", "-o", report)
    if result.code != 0:
        fail(f"coverage json exited {result.code}:\n{result.stdout}{result.stderr}")
    data = json.loads(Path(report).read_text("utf-8"))
    Path(report).unlink(missing_ok=True)
    totals: dict[str, list[int]] = {package.path: [0, 0] for package in packages}
    for filename, entry in data.get("files", {}).items():
        for package in packages:
            if filename.startswith(f"{package.path}/src/"):
                summary = entry.get("summary", {})
                totals[package.path][0] += int(summary.get("covered_lines", 0)) + int(
                    summary.get("covered_branches", 0)
                )
                totals[package.path][1] += int(summary.get("num_statements", 0)) + int(
                    summary.get("num_branches", 0)
                )
                break
    return {
        path: (100.0 * covered / statements if statements else 100.0)
        for path, (covered, statements) in totals.items()
    }


def report_coverage(root: Path, packages: tuple[Package, ...]) -> None:
    """Print each package's local coverage beside its floor, no verdict.

    One machine's run misses the platform branches and the task
    shells only the measured CI union reaches, so the local number is
    a low-biased preview: it informs, and the aggregating CI job's
    union is what the floors gate.
    """
    from livery.workshop._coverage_store import WORKSPACE_TESTS

    measured = measured_coverage(root, packages)
    for package in packages:
        if package.path == WORKSPACE_TESTS:
            continue  # a unit of the union, never a package with a floor
        policy = coverage_policy(package)
        if policy is None:
            continue
        percent = measured.get(package.path, 0.0)
        judged = (
            "the mark on the store judges the CI union"
            if policy.ratchet
            else f"floor {policy.floor:.1f}% judges the CI union"
        )
        print(f"  coverage {package.path}: {percent:.1f}% here ({judged})")


#: The gate job's scratch directory for the union's inputs: one
#: directory per leg, the leg's fresh units and the units carried from
#: main's record as coverage data files. Ignored by the template's
#: gitignore, written afresh by every union.
COVERAGE_DATA = "coverage-data"


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


def suites_of(packages: tuple[Package, ...]) -> tuple[Package, ...]:
    """The packages with a test directory: the units the record keys."""
    return tuple(
        package for package in packages if (package.directory / "tests").is_dir()
    )


def units_of(root: Path, packages: tuple[Package, ...]) -> tuple[Package, ...]:
    """The recorded units: every package's suite, then the workspace's own tests."""
    from livery.workshop._coverage_store import workspace_suite

    unit = workspace_suite(root)
    return suites_of(packages) + ((unit,) if unit is not None else ())


def suite_arcs_by_context(
    data: Path, packages: tuple[Package, ...], package: Package, *, root: Path
) -> dict[str, list[tuple[int, int]]]:
    """The arcs *package*'s suite recorded in the leg's data, for its closure's files.

    Two sources make the suite's arcs: the contexts of its own tests
    (the node ids under ``<package>/tests/``, as the workshop's pytest
    plugin names them), and the arcs recorded under no context at all,
    which run at import time before any test and belong to whichever
    suites' closures hold their files. Files outside the closure are
    not the suite's to store. The names are workspace-relative,
    whichever way the meter stored them. An arc is a pair of line
    numbers, so the row carries statements and branches alike.
    """
    import re

    from coverage import CoverageData

    from livery.workshop._coverage_store import WORKSPACE_TESTS, in_closure

    measured = CoverageData(basename=str(data))
    measured.read()
    files: dict[str, list[tuple[int, int]]] = {}
    own = (
        rf"^{re.escape(package.path)}/"
        if package.path == WORKSPACE_TESTS
        else rf"^{re.escape(package.path)}/tests/"
    )
    for query in ([own], [r"^$"]):
        measured.set_query_contexts(query)
        for filename in measured.measured_files():
            name = _relative(filename, root)
            if name is None or not in_closure(packages, package, name):
                continue
            arcs = measured.arcs(filename) or []
            if arcs:
                found = {(a, b) for a, b in arcs}
                files[name] = sorted(set(files.get(name, [])) | found)
    measured.set_query_contexts(None)
    return files


def suites_that_ran(
    marker: dict[str, Any], root: Path, packages: tuple[Package, ...]
) -> tuple[Package, ...]:
    """The units the leg ran, by its scope marker.

    Every suite after a full gate, the named packages' suites after a
    narrowed or a measured one, none after a skip; the workspace's
    own tests whenever any suite ran, since every leg that runs a
    suite runs them. The marker decides, never the data's contexts: a
    suite whose tests reach no line that collection had not already
    reached leaves no context of its own under a first-hit tracer,
    and still ran.
    """
    from livery.workshop._coverage_store import workspace_suite
    from livery.workshop._verified import AFFECTED, FULL, MEASURED

    scope = marker["scope"]
    if scope == FULL:
        ran = suites_of(packages)
    elif scope in (AFFECTED, MEASURED):
        named = set(marker["packages"])
        ran = tuple(package for package in suites_of(packages) if package.path in named)
    else:
        return ()
    unit = workspace_suite(root)
    return ran + ((unit,) if unit is not None else ())


def _relative(filename: str, root: Path) -> str | None:
    name = filename.replace("\\", "/")
    if Path(name).is_absolute():
        try:
            return Path(name).relative_to(root).as_posix()
        except ValueError:
            return None
    return name


#: The leg's own combined data, read for the split and never uploaded:
#: named outside the ``.coverage.*`` glob the leg's combine consumes.
SUITES_DATA = "coverage-suites.db"


def _put_leg(
    root: Path,
    marker: dict[str, Any],
    units: dict[str, Any],
    *,
    timing: dict[str, Any] | None = None,
) -> None:
    """Put the leg's scope and *units* on its per-run ref; red when it cannot.

    Outside CI nothing is put and the line says so. Inside CI a leg
    that measured and cannot put its lines is red: the union would
    otherwise lack a suite, and a smaller union never passes. The
    *timing* row, when the leg has one, rides the same write.
    """
    from livery.workshop._coverage_store import put_run
    from livery.workshop._state import run_context

    run = run_context()
    leg = marker["leg"]
    if run is None:
        print(
            f"  coverage store: {len(units)} unit(s) measured; outside CI nothing"
            " is stored"
        )
        return
    why = put_run(
        root,
        run,
        leg=leg,
        scope=marker["scope"],
        packages=tuple(marker["packages"]),
        units=units,
        timing=timing,
    )
    if why:
        fail(
            f"the leg's lines could not be put on its per-run ref ({why}): a leg"
            " that measured a suite and cannot store its lines is red, never a"
            " smaller union"
        )
    print(
        f"  coverage store: {len(units)} unit(s) on the run's ref for"
        f" {leg or 'an unlabelled leg'} ({marker['scope']})"
    )


def store_suites(
    root: Path,
    packages: tuple[Package, ...],
    *,
    marker: dict[str, Any],
    timing: dict[str, Any] | None = None,
) -> None:
    """Split the leg's data per unit and put every unit on the leg's per-run ref.

    The leg's parts combine into `SUITES_DATA` first (kept apart from
    the leg's own combine); each unit the marker says ran is split out
    and its lines within its closure ride the per-run ref under the
    unit's closure identity, with the scope the gate ran. Only a CI
    run puts; a put that fails is red.
    """
    from coverage import CoverageData

    from livery.workshop._coverage_store import Unit, closure_id
    from livery.workshop._git_ops import GitOps
    from livery.workshop._state import run_context

    leg = marker["leg"]
    parts = sorted(root.glob(".coverage.*"))
    if not parts:
        return
    combined = CoverageData(basename=str(root / SUITES_DATA))
    for part in parts:
        piece = CoverageData(basename=str(part))
        piece.read()
        combined.update(piece)
    combined.write()
    run = run_context()
    git = GitOps(root)
    measured: dict[str, Unit] = {}
    for package in suites_that_ran(marker, root, packages):
        files = suite_arcs_by_context(root / SUITES_DATA, packages, package, root=root)
        if run is None:
            print(
                f"  coverage store: {package.path} measured ({len(files)} files);"
                " outside CI nothing is stored"
            )
            continue
        key = closure_id(git, packages, package)
        measured[package.path] = Unit(
            package.path, key, run.run_id, git.head_sha(), files
        )
    (root / SUITES_DATA).unlink(missing_ok=True)
    if run is None:
        return
    _put_leg(root, marker, measured, timing=timing)
    for unit in measured.values():
        print(
            f"  coverage store: {unit.path} stored for closure {unit.closure[:12]}"
            f" on {leg} ({len(unit.files)} files)"
        )


def combine_leg(
    root: Path,
    packages: tuple[Package, ...],
    *,
    timing: dict[str, Any] | None = None,
) -> None:
    """Combine the leg's data into ``.coverage``, each suite's lines put first.

    Every suite the leg ran is combined apart and put on the leg's
    per-run ref under the leg's label (the marker's), so the gate
    job's union reads it there; then every data file combines into
    the leg's ``.coverage``. Refuses when a leg that ran its gate left
    no data, naming the variable that arms the meter; a leg whose
    gate skipped (a tree already proved, or nothing affected)
    legitimately measured nothing, says so, and puts its scope alone,
    so the union knows the leg skipped rather than died. A workspace
    with no packages (a project just born) runs its own tests
    unmetered, and they reach no package source; that leg puts its
    units with no lines, so the union finds what the leg ran. The
    leg's
    *timing* row, when it has one, rides the one write with the
    scope; the marker's scope goes on it, as the stamp reads it.
    """
    from livery.workshop._verified import NOTHING, VERIFIED, read_marker

    parts = sorted(root.glob(".coverage.*"))
    marker = read_marker(root)
    scope = marker["scope"]
    if timing is not None:
        timing = {**timing, "scope": marker}
    if not parts and not (root / ".coverage").is_file():
        if not packages:
            # The workspace's own tests ran, unmetered, and reached no
            # package source: each unit the leg ran is put with no
            # lines, so the union finds what ran and judges nothing.
            from livery.workshop._coverage_store import Unit, closure_id
            from livery.workshop._git_ops import GitOps
            from livery.workshop._state import run_context

            print(
                "  coverage: the workspace has no packages to measure; its own"
                " tests ran unmetered"
            )
            run = run_context()
            empty: dict[str, Unit] = {}
            if run is not None:
                git = GitOps(root)
                for unit in units_of(root, ()):
                    empty[unit.path] = Unit(
                        unit.path,
                        closure_id(git, (), unit),
                        run.run_id,
                        git.head_sha(),
                        {},
                    )
            _put_leg(root, marker, empty, timing=timing)
            return
        if scope not in (VERIFIED, NOTHING):
            fail(
                "this leg left no coverage data: nothing was metered. Inside CI"
                " the test runner arms COVERAGE_PROCESS_START in pytest's"
                " environment, so the tests and every process they start are"
                " metered; a leg that ran its gate and left no data ran no"
                " metered pytest, and there is nothing to union."
            )
        print(f"  coverage: no data, the gate ran {scope!r}; nothing to combine")
        _put_leg(root, marker, {}, timing=timing)
        return
    store_suites(root, packages, marker=marker, timing=timing)
    if parts:
        result = toolroom.coverage.opts(
            cwd=root, env=_unmetered(), nofail=True, recorded=False
        )("combine")
        if result.code != 0:
            fail(
                f"coverage combine exited {result.code}:\n"
                f"{result.stdout}{result.stderr}"
            )
    print(f"  coverage: {len(parts)} data file(s) combined into .coverage")


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


def combine_union(root: Path, packages: tuple[Package, ...]) -> tuple[Package, ...]:
    """Union the run's legs' lines with the records; the packages it judges.

    Each check leg's per-run ref carries the scope its gate ran and
    the units it measured. A full leg measured every suite, a narrowed
    or measured leg the suites its marker names; a leg whose gate
    skipped (a tree already proved, or nothing affected) measured
    nothing. Every unit a leg did not measure is carried from a record
    on that leg, the branch's own before main's, at the unit's current
    closure identity, so the union is the same global union a full run
    produces and every package is judged. A unit no record can supply
    refuses by name: a leg skips a suite only when a record holds it,
    so a miss here is a leg that narrowed without the records, or a
    record moved on since, never a smaller union.

    The union is written back per leg: a pull request's run onto its
    branch's record, main's run onto main's. The leg's fresh units
    replace their rows, the rows carried from the other record are
    copied in, the target's own carried rows stand, and the rows of
    units that no longer exist go. On main's run the branch is the one
    the verified record names for the tree, so the merge copies the
    branch's record into main's. A record that cannot be written
    prints why: the next run reruns what it cannot reuse, the safe
    direction.

    Refuses by name when no leg left its lines, when a leg's ref
    carries no readable file, when a leg names no scope the union
    reads, when a leg that ran a suite left its lines out, and when a
    unit is neither the run's nor any record's.

    Returns:
        The judged packages, in *packages* order; empty when the
        workspace has no unit to union, and then no union file is
        written.
    """
    from livery.workshop._coverage_store import (
        RUN_FILE,
        Unit,
        closure_id,
        put_record,
        row_name,
        run_legs,
    )
    from livery.workshop._git_ops import GitOps
    from livery.workshop._state import run_context
    from livery.workshop._verified import (
        AFFECTED,
        FULL,
        MEASURED,
        NOTHING,
        VERIFIED,
    )

    run = run_context()
    if run is None:
        fail(
            "the union reads the run's per-run refs, and outside CI there is no"
            " run: the legs' lines are unioned and judged inside CI alone"
        )
    legs, why = run_legs(root, run)
    if why:
        fail(f"the run's per-run refs could not be read ({why}); nothing to union")
    if not legs:
        fail(
            f"no check leg left its lines on run {run.run_id}'s refs: every leg"
            f" puts its scope and its lines there ({RUN_FILE}) at its end, and"
            " a missing put is a red leg, never a smaller union"
        )
    scratch = root / COVERAGE_DATA
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir()
    git = GitOps(root)
    units = units_of(root, packages)
    keys = {unit.path: closure_id(git, packages, unit) for unit in units}
    bases, target = _record_bases(root, git, run)
    collected: list[Path] = []
    reused: list[Path] = []
    ran = 0
    writes: list[tuple[str, dict[str, Unit], dict[str, tuple[str, Unit]], Record]] = []
    for leg in sorted(legs, key=lambda item: item.key):
        if leg.why:
            fail(
                f"leg {leg.key}: {leg.why}; the leg died before its lines, or"
                " its put failed, and a missing put is a red leg, never a"
                " smaller union"
            )
        if not leg.label:
            fail(
                f"leg {leg.key} names no label, so no record can supply what it"
                " skipped; the job runner sets WORKSHOP_LEG for every leg"
            )
        if leg.scope in (VERIFIED, NOTHING):
            print(f"  coverage: leg {leg.label} ran {leg.scope!r}: no suite, no data")
        elif leg.scope in (FULL, AFFECTED, MEASURED):
            marker = {"scope": leg.scope, "packages": list(leg.packages), "leg": ""}
            expected = suites_that_ran(marker, root, packages)
            missing = [unit.path for unit in expected if unit.path not in leg.units]
            if missing:
                fail(
                    f"leg {leg.label} ran {leg.scope!r} and its ref lacks"
                    f" {', '.join(missing)}: a leg that ran a suite puts its"
                    " lines, and a missing put is a red leg, never a smaller union"
                )
            ran += 1
            for path, unit in sorted(leg.units.items()):
                collected.append(
                    _write_unit(
                        scratch / leg.label, f"fresh-{slug(path)}.coverage", unit.files
                    )
                )
        else:
            fail(
                f"leg {leg.label}: its ref names a scope the union does not read"
                f" ({leg.scope!r}); the check legs leave full, affected, nothing,"
                " verified, or measured"
            )
        pending = [unit for unit in units if unit.path not in leg.units]
        held: dict[str, Record] = {}
        carried: dict[str, tuple[str, Unit]] = {}
        for suite in pending:
            key = keys[suite.path]
            states: list[str] = []
            for base in bases:
                row = _read_record(root, held, base, leg.label).units.get(suite.path)
                if row is not None and row.closure == key:
                    carried[suite.path] = (base, row)
                    break
                states.append(
                    f"{base} only at {row.closure[:12]}"
                    if row is not None
                    else f"{base} none"
                )
            else:
                fail(
                    f"leg {leg.label}: no record holds {suite.path} at closure"
                    f" {key[:12]} ({', '.join(states)}). A leg skips a suite"
                    " only when a record holds it at the suite's closure, so"
                    " this leg narrowed without the records, or they moved on"
                    " since; a run of the full gate measures it again."
                )
            base, row = carried[suite.path]
            reused.append(
                _write_unit(
                    scratch / leg.label, f"reuse-{slug(suite.path)}.coverage", row.files
                )
            )
            print(
                f"  coverage: {suite.path} on {leg.label}: reused from run"
                f" {row.run} ({len(row.files)} files), {base}'s record"
            )
        if target:
            own = _read_record(root, held, target, leg.label)
            writes.append((leg.label, dict(leg.units), carried, own))
    if not collected and not reused:
        print("  coverage: no unit to union; nothing judged")
        return ()
    scrubbed = _unmetered()
    result = toolroom.coverage.opts(
        cwd=root, env=scrubbed, nofail=True, recorded=False
    )("combine", *(str(path) for path in collected + reused))
    if result.code != 0:
        fail(f"coverage combine exited {result.code}:\n{result.stdout}{result.stderr}")
    report = toolroom.coverage.opts(
        cwd=root, env=scrubbed, nofail=True, recorded=False
    )("report", "--sort=cover")
    print(report.stdout.rstrip())
    print(f"  coverage: the union of {ran} leg(s) and {len(reused)} reused suite(s)")
    if not target:
        print(
            f"  coverage record: not written, a {run.event or 'local'} run"
            " that names no branch has no record of its own"
        )
        return tuple(packages)
    # Two runs of one branch overlap when a push follows a push: the
    # older run's gate job can finish last, and its rows would replace
    # the newer run's. Only the run of the branch's current head writes.
    moved, head = _moved_on(git, run, target)
    if moved:
        print(
            f"  coverage record: {target}'s head moved on to {moved[:12]} since"
            f" this run's {head[:12]}; not written, the newer run writes"
        )
        return tuple(packages)
    for label, fresh, carried, own in writes:
        stale = own.stale(keys)
        # The rows carried from another record are copied in; the
        # target's own carried rows already stand. A stale row a new
        # row replaces by name (a row of an older shape, say) is
        # replaced, not removed: only the rest go.
        rows = dict(fresh)
        rows.update(
            {path: row for path, (base, row) in carried.items() if base != target}
        )
        removed = [name for name in stale if name not in {row_name(p) for p in rows}]
        if not rows and not stale:
            print(
                f"  coverage record: {target}/{label}: unchanged, {len(carried)}"
                " carried"
            )
            continue
        why = put_record(root, run, leg=label, fresh=rows, remove=stale, base=target)
        if why:
            print(
                f"  coverage record: {target}/{label} not written ({why}); the"
                " next run reruns what it cannot reuse"
            )
            continue
        print(
            f"  coverage record: {target}/{label}: {len(fresh)} fresh,"
            f" {len(carried)} carried, {len(removed)} removed"
        )
    return tuple(packages)


def _read_record(root: Path, held: dict[str, Record], base: str, label: str) -> Record:
    """*base*'s record on the leg *label*, read once into *held*; red if unreadable."""
    from livery.workshop._coverage_store import recorded

    if base not in held:
        held[base] = recorded(root, leg=label, base=base)
        if held[base].failed:
            fail(
                f"{base}'s record on {label} could not be read"
                f" ({held[base].reason}); the suites the leg skipped cannot be"
                " supplied, and a smaller union never passes"
            )
        for skipped in held[base].skipped:
            print(f"  coverage: {base}/{label}: {skipped}")
    return held[base]


def _moved_on(git: GitOps, run: RunContext, branch: str) -> tuple[str, str]:
    """*branch*'s head on origin and this run's, when they differ; empty otherwise.

    This run's head is the pull request's on a pull request and the
    checkout on a push. Empty too when origin cannot be asked: an
    answer the run cannot get never withholds a write on its own.
    """
    from livery.workshop._git_ops import GitError

    try:
        head = run.head_sha if run.event == "pull_request" else git.head_sha()
        git.fetch()
        current = git.remote_head(branch)
    except GitError:
        return "", ""
    return (current, head) if current and current != head else ("", "")


def _record_bases(
    root: Path, git: GitOps, run: RunContext
) -> tuple[tuple[str, ...], str]:
    """The records the union carries from, in order, and the one it writes.

    A pull request's run reads its branch's record before main's and
    writes its branch's; a run naming no branch reads main's and
    writes nothing. Main's run reads the record of the branch the
    verified row names for its tree, the branch just merged, before
    main's, and writes main's: that is the copy at the merge. A push
    of a tree the record does not name, a stale squash, reads main's
    alone, and its full gate measured everything anyway.
    """
    from livery.workshop._coverage_store import MAIN
    from livery.workshop._quality import record_bases
    from livery.workshop._verified import record, tree_id

    if run.event == "push":
        row, why = record(root, tree_id(git))
        branch = row.branch if row is not None else ""
        if why:
            print(f"  coverage record: the verified row could not be read ({why})")
        elif branch:
            print(
                f"  coverage record: {MAIN} takes {branch}'s record for the tree"
                " it proved"
            )
        return record_bases(branch), MAIN
    if run.event == "pull_request" and run.head_ref:
        return record_bases(run.head_ref), run.head_ref
    return (MAIN,), ""


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
                _write_unit(into / label, f"reuse-{slug(path)}.coverage", unit.files)
            )
    return written, misses


def enforce_coverage(root: Path, packages: tuple[Package, ...]) -> dict[str, float]:
    """Fail any package measurably below its floor; the measured percentages.

    A committed floor passes at the floor minus the package's epsilon.
    Under auto-ratchet the floor is the mark on the store minus
    epsilon: a run with no mark records one and passes, a run that
    clears the mark by more than epsilon raises it (a CI run writes;
    a local run says it would), and a store that cannot be read falls
    open with its reason and writes nothing. Every verdict prints with
    its numbers, so the numbers on screen are the numbers enforced.
    """
    from livery.workshop import _coverage_marks
    from livery.workshop._state import run_context

    measured = measured_coverage(root, packages)
    policies = {package.path: coverage_policy(package) for package in packages}
    ratcheted = [
        p for p in packages if (policies[p.path] or FloorPolicy(None, False, 0)).ratchet
    ]
    current: dict[str, _coverage_marks.Mark] | None = {}
    marks_why = ""
    if ratcheted:
        current, marks_why = _coverage_marks.marks(root)
    run = run_context()
    problems: list[str] = []
    for package in packages:
        policy = policies[package.path]
        if policy is None:
            continue
        percent = measured.get(package.path, 0.0)
        if not policy.ratchet:
            assert policy.floor is not None
            print(
                f"  coverage {package.path}: {percent:.1f}%"
                f" (floor {policy.floor:.1f}%, epsilon {policy.epsilon}%)"
            )
            if percent < policy.floor - policy.epsilon:
                problems.append(
                    f"{package.path}: {percent:.1f}% is below the committed"
                    f" floor of {policy.floor:.1f}% by more than the"
                    f" {policy.epsilon}% epsilon"
                )
            continue
        if current is None:
            print(
                f"  coverage {package.path}: {percent:.1f}% (auto-ratchet:"
                f" {marks_why}; the gate falls open and records nothing)"
            )
            continue
        (verdict,) = _coverage_marks.judge(
            {package.path: percent}, current, {package.path: policy.epsilon}
        )
        print(_coverage_marks.render(verdict))
        if not verdict.ok:
            assert verdict.mark is not None
            problems.append(
                f"{package.path}: {percent:.1f}% is below its mark of"
                f" {verdict.mark.value:.1f}% by more than the {policy.epsilon}%"
                f" epsilon; raise the code, or lower the mark deliberately with"
                f" `{footman.prog()} coverage.accept {package.path} <value>"
                " --reason=...`"
            )
            continue
        if verdict.raises:
            if run is None:
                print("    (a CI run records the mark; this run does not)")
                continue
            why = _coverage_marks.write_mark(
                root,
                package=package.path,
                value=percent,
                kind="first" if verdict.mark is None else "ratchet",
                by=f"run {run.run_id}",
            )
            print(f"    {'recorded' if not why else 'not recorded: ' + why}")
    if problems:
        fail(
            "coverage fell below the floors:\n  "
            + "\n  ".join(problems)
            + "\n  raise the code, or lower a floor deliberately"
        )
    return measured


def scoped_rewrite(subset: tuple[Package, ...]) -> None:
    """Run the python kind's rewriters over *subset*, serially.

    Format and lint rewrite the same files, so their order is this
    backend's knowledge: format first, lint's fixes second. The
    caller runs rewrites before any kind's checks read the tree.
    """
    from livery.footman import step

    paths = package_paths(subset)
    # The record-only block: the calls run bare and serial, and the
    # record keeps the title, verdict, and duration.
    with step("format"):
        run_format(check=False, paths=paths)
    with step("lint"):
        run_lint(fix=True, paths=paths)


def scoped_gate(
    subset: tuple[Package, ...], *, root: Path, check_style: bool = True
) -> None:
    """Run the python kind's checks over *subset*, composed here.

    The verbs, their titles, and what runs in parallel are this
    backend's knowledge: everything fans out together, and
    [livery.workshop._backends._python.run_typecheck][] nests its
    own fan-out inside. ``check_style`` is off when a rewrite pass
    already ran, where re-judging the style it just wrote would
    only spend time agreeing.

    The steps are built at call time, so the property tests that
    patch this module's verbs keep gating the composition.
    """
    from livery.footman import parallel, step
    from livery.workshop._coverage_store import WORKSPACE_TESTS
    from livery.workshop._kinds import gated

    # The workspace's own tests are a unit of this gate with no kind:
    # formatted, linted, type-checked, and run, never type-complete.
    members = tuple(package for package in subset if package.path != WORKSPACE_TESTS)
    unit = tuple(package for package in subset if package.path == WORKSPACE_TESTS)
    paths = package_paths(subset)
    type_paths = package_paths(gated(members, "typecheck") + unit)
    complete = gated(members, "typecomplete")
    tested = gated(members, "test") + unit
    with parallel() as p:
        if check_style:
            p(step(run_format, title="format")(check=True, paths=paths))
            p(step(run_lint, title="lint")(paths=paths))
        p(step(run_typecheck, title="typecheck")(paths=type_paths))
        p(step(run_typecomplete, title="typecomplete")(complete))
        p(step(run_test, title="test")(packages=tested, root=root, scoped=True))


def run_test(
    *pytest_args: str,
    packages: tuple[Package, ...] = (),
    root: Path | None = None,
    scoped: bool = False,
) -> None:
    """Run the test suite; *pytest_args* forwarded verbatim.

    With *packages* and *root*, the run measures coverage over
    ``livery``: inside CI the tests run metered for the gate job's
    union, and on a machine the run enforces each package's committed
    floor afterwards. *scoped* additionally narrows collection to
    those packages' own test directories (the affected mode). Without
    them the arguments pass through untouched.
    """
    if not packages or root is None:
        pytest.opts(in_process=False)(*pytest_args)
        return
    dirs: tuple[str, ...] = ()
    if scoped:
        # The workspace's own tests ride every scoped run: they reach
        # any package, so no narrowing excuses them, and the leg stores
        # them as a unit keyed by the whole tree.
        from livery.workshop._coverage_store import WORKSPACE_TESTS

        dirs = tuple(
            f"{package.path}/tests"
            for package in packages
            if (package.directory / "tests").is_dir()
        ) + ((WORKSPACE_TESTS,) if (root / WORKSPACE_TESTS).is_dir() else ())
    from livery.workshop._pytest_contexts import ARMED
    from livery.workshop._pytest_speed import FILE_VARIABLE
    from livery.workshop._state import run_context

    run = run_context()
    with tempfile.TemporaryDirectory() as scratch:
        # The workshop's speed plugin sums each package's test time
        # into this file; the sums print beside the marks afterwards,
        # on a red run too.
        sums = Path(scratch) / "speed.json"
        env = {**os.environ, FILE_VARIABLE: str(sums)}
        try:
            if run is not None:
                # Inside CI the tests run metered, and only they.
                # Coverage's own subprocess variable is armed in
                # pytest's environment, never in the gate's own, so
                # every python the tests start inherits it (the xdist
                # workers, the runner's children a test spawns in a
                # fixture workspace) and the driver that decides what
                # to run records nothing: a line counts only when a
                # test reached it, whatever the gate ran around the
                # tests. The workshop's pytest plugin names each
                # test's context in that run, the leg splits the one
                # run's data per suite, and the floors are judged
                # once, on the union, in the gate job.
                armed = {**env, ARMED: str(root / "pyproject.toml")}
                pytest.opts(in_process=False, env=armed)(*dirs, *pytest_args)
            else:
                # Bare --cov: the measured source is [tool.coverage.run]
                # source, the namespace the render derived, never a
                # spelled module.
                pytest.opts(in_process=False, env=env)(
                    *dirs, "--cov", "--cov-report=", *pytest_args
                )
        finally:
            for line in speed_lines(root, sums, leg=run.leg if run else ""):
                print(line)
    if run is None:
        report_coverage(root, packages)


def speed_lines(root: Path, sums: Path, *, leg: str = "") -> list[str]:
    """Each package's summed test time beside its mark on *leg*; the lines.

    *sums* is the file the speed plugin wrote. Without *leg* the mark
    shown is the reference leg's. An unreadable store says so once and
    the times still print; no file means the plugin did not run.
    """
    from livery.workshop import _speed
    from livery.workshop._points import check_legs

    if not sums.is_file():
        return []
    try:
        loaded = json.loads(sums.read_text("utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(loaded, dict) or not loaded:
        return []
    if not leg:
        legs = check_legs(root)
        leg = next((item for item in legs if _speed.is_reference(item)), "")
        if not leg and legs:
            leg = legs[0]
    current, why = _speed.marks(root)
    lines: list[str] = []
    if current is None:
        lines.append(f"  speed marks: not read ({why})")
    for package, data in sorted(loaded.items()):
        if not isinstance(data, dict):
            continue
        seconds = float(data.get("seconds", 0.0))
        tests = int(data.get("tests", 0))
        mark = current.get((str(package), leg)) if current else None
        beside = (
            f"mark {mark.seconds:.1f}s on {leg}"
            if mark is not None
            else f"no mark on {leg}"
            if current is not None
            else "mark unknown"
        )
        lines.append(f"  speed {package}: {seconds:.1f}s over {tests} tests ({beside})")
    return lines


def declared_requirements(package: Package) -> dict[str, str]:
    """The package's declared dependencies, name to constraint text.

    Read from ``[project.dependencies]``; the layering lint compares
    these against the contract's ``[[depends]]`` edges.
    """
    entries: dict[str, str] = {}
    pyproject = package.directory / "pyproject.toml"
    if not pyproject.is_file():
        return entries
    data = tomllib.loads(pyproject.read_text("utf-8"))
    for requirement in data.get("project", {}).get("dependencies", []):
        text = str(requirement)
        name = text
        for cut in "[>=<!~; ":
            head, _, _ = name.partition(cut)
            name = head
        entries[name] = text[len(name) :].split(";")[0].replace("]", "")
    return entries


def check(package: Package, root: Path) -> None:
    """Nothing: python's gate verbs run at workspace scope.

    ruff, the four checkers, and pytest each cover every python
    package in one invocation from the quality verbs; the kind
    declares no ``kind_verbs``, so the gate never calls this. It
    exists to satisfy livery.workshop._kinds.Backend.
    """
    del package, root


def build(package: Package, root: Path, *, epoch: int = 0) -> Path:
    """Build *package*'s wheel and sdist into its ``dist/``; the dist dir.

    Always from a clean ``dist/``: reusing an artifact from an
    earlier build installs stale code under current tests, a silent
    footgun bought off for seconds of build. *epoch* (a commit's
    timestamp) rides ``SOURCE_DATE_EPOCH`` so a pure-Python rebuild
    at the same ref is byte-identical.
    """
    import shutil

    from livery.workshop._docs import materialise_module_docs

    # The wheel-embedded _docs refresh whole from packages/<name>/docs
    # here, so the wheel can never carry docs older than the tree it
    # was built from. Machine territory: gitignored, never hand-edited.
    materialise_module_docs(package)
    dist = package.directory / "dist"
    shutil.rmtree(dist, ignore_errors=True)
    env = dict(os.environ)
    if epoch:
        env["SOURCE_DATE_EPOCH"] = str(epoch)
    result = toolroom.uv.opts(
        cwd=package.directory, nofail=True, recorded=False, env=env
    )("build", "--out-dir", str(dist))
    if result.code != 0:
        fail(
            f"uv build ({package.name}) exited {result.code}:\n"
            f"{result.stdout}{result.stderr}"
        )
    if not list(dist.glob("*.whl")):
        fail(f"{package.name}: the build produced no wheel in {dist}")
    return dist


def publish_artifact(
    package: Package, *, version: str, publish_url: str, token: str, local: bool
) -> bool:
    """Upload ``dist/*`` to the python index; the kind's publish seam.

    ``uv publish``, through the wave's own uploader: *version* and
    *local* are other kinds' fields, since the wheels in ``dist/``
    already carry their versions and a python index is never a
    folder.
    """
    from livery.workshop._publish import publish_wheels

    return publish_wheels(package, index_url=publish_url, token=token)


def _index_args(root: Path) -> tuple[str, ...]:
    """The repo's ``[[tool.uv.index]]`` entries as install flags.

    The isolated venv is bare and reads no project config, so without
    this it resolves only from PyPI plus the local wheels, and a
    custom index's already-published packages cannot resolve the way
    a real consumer's do.
    """
    import tomllib

    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        return ()
    data = tomllib.loads(pyproject.read_text("utf-8"))
    args: list[str] = []
    for entry in (data.get("tool", {}).get("uv", {}).get("index", [])) or []:
        url = str(entry.get("url") or "")
        if not url:
            continue
        args.append("--default-index" if entry.get("default") else "--index")
        args.append(url)
    return tuple(args)


def _dev_pins(root: Path, scratch: Path) -> Path | None:
    """The lock's dev-group resolution as exact pins; None without one.

    ``uv export`` reads the workspace lock offline, so the isolated
    venv's toolchain arrives at the versions the gate itself tested
    with. Workspace members are excluded: the leg already installed
    the released wheel and its floors, the members export as
    workspace-relative paths a scratch venv cannot resolve, and
    reinstalling them would clobber the starved resolution the
    movement guard protects. A workspace without a lock or a dev
    group (a bare rig, a consumer checkout) answers None and the leg
    falls back to a bare pytest install.
    """
    pins = scratch / "dev-pins.txt"
    result = toolroom.uv.opts(cwd=root, nofail=True, recorded=False)(
        "export",
        "--format",
        "requirements-txt",
        "--only-group",
        "dev",
        "--no-emit-project",
        "--no-emit-workspace",
        "--no-hashes",
        "-o",
        str(pins),
    )
    if result.code != 0 or not pins.is_file():
        return None
    # A path-sourced dev entry (a checkout standing in for a wheel)
    # exports as a local reference the scratch venv can neither
    # reach nor parse; the toolchain pins we want are the
    # index-resolvable lines, so the local ones are dropped.
    lines = [
        line
        for line in pins.read_text("utf-8").splitlines()
        if "file://" not in line and not line.startswith(("-e ", "./", "/"))
    ]
    pins.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return pins


def _pins_without(pins: Path, resolved: dict[str, str]) -> Path:
    """Rewrite *pins* without the distributions the leg already resolved.

    The dev group's export carries every member's dependencies at the
    locked version; installed over a leg, those would move the floor
    or the latest the leg resolved and the two legs would prove one
    set. The toolchain the tests need is what remains: pytest, its
    plugins, and whatever else the leg did not resolve, each pinned
    from the lock. The movement guard stays behind this as the
    backstop for a tool that genuinely shares a dependency with the
    member and needs another version of it.
    """
    taken = {name.lower().replace("_", "-") for name in resolved}
    kept: list[str] = []
    for line in pins.read_text("utf-8").splitlines():
        head = line.strip().split(";", 1)[0]
        name = re.split(r"[\s=<>!~\[]", head, maxsplit=1)[0].lower().replace("_", "-")
        if name and name in taken:
            continue
        kept.append(line)
    pins.write_text("\n".join(kept) + "\n", encoding="utf-8")
    return pins


def _direct_requirements(package: Package) -> tuple[str, ...]:
    """The requirement strings *package*'s ``[project]`` declares.

    The isolated install lists them explicitly beside the wheel: to
    the resolver a wheel file's own dependencies are transitive, so
    ``--resolution=lowest-direct`` would leave them at highest and
    the floor leg would starve nothing. Named on the command line
    they are direct, and the starvation lands where the leg aims it.
    """
    import tomllib

    pyproject = package.directory / "pyproject.toml"
    if not pyproject.is_file():
        return ()
    data = tomllib.loads(pyproject.read_text("utf-8"))
    return tuple(
        str(requirement)
        for requirement in data.get("project", {}).get("dependencies", []) or []
    )


def _leg_env(root: Path, venv: Path) -> dict[str, str]:
    """The isolated leg's process environment, scrubbed of the workspace.

    The leg's own venv leads PATH and the workspace venv's entries
    drop out, so a tool probe answers for what the leg installed
    rather than what the workspace happens to carry (a click tool
    found on the workspace PATH but absent from the leg's python
    extracts a different spec). VIRTUAL_ENV points at the leg, and
    the ambient coverage variables go the way `measured_coverage`
    sends them: a metered gate must not re-point the leg's children.
    """
    workspace_venv = str(root / ".venv")
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("COVERAGE_", "COV_CORE_"))
    }
    kept = [
        entry
        for entry in env.get("PATH", "").split(os.pathsep)
        if entry
        and entry != workspace_venv
        and not entry.startswith(workspace_venv + os.sep)
    ]
    from livery.workshop._pythons import scripts_dir

    env["PATH"] = os.pathsep.join([str(scripts_dir(venv)), *kept])
    env["VIRTUAL_ENV"] = str(venv)
    return env


def _install_target(package: Package, wheel: Path) -> str:
    """The leg's install spelling for the wheel under test.

    A package may declare a ``test`` extra naming what its suite needs
    beyond its runtime dependencies (an optional host it drives, the
    editor-intelligence libraries a doc probe imports). The leg runs
    that suite, so it installs the wheel with the extra; without one
    the wheel installs plain.
    """
    import tomllib

    pyproject = package.directory / "pyproject.toml"
    if pyproject.is_file():
        data = tomllib.loads(pyproject.read_text("utf-8"))
        extras = data.get("project", {}).get("optional-dependencies", {}) or {}
        if "test" in extras:
            return f"{package.name}[test] @ {wheel.resolve().as_uri()}"
    return str(wheel)


def _direct_versions(package: Package, resolved: dict[str, str]) -> dict[str, str]:
    """The resolved versions of *package*'s own direct dependencies."""
    import re

    names = []
    for requirement in _direct_requirements(package):
        match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", requirement)
        if match:
            names.append(match.group(1).lower().replace("_", "-"))
    return {
        name: version
        for name, version in resolved.items()
        if name.lower().replace("_", "-") in names
    }


def _refresh_args(release_dirs: tuple[Path, ...]) -> list[str]:
    """The ``--refresh-package`` flags for every wheel in *release_dirs*.

    uv keys its wheel cache on the distribution's filename, and a
    co-released member rebuilt at the same version keeps the same
    filename: without a refresh the leg installs the first build the
    cache saw, and every later source fix tests as if it did nothing.
    One flag per distribution named by a wheel in the set's ``dist/``
    directories, sorted; empty without any.
    """
    names = {
        wheel.name.split("-", 1)[0].replace("_", "-").lower()
        for directory in release_dirs
        for wheel in directory.glob("*.whl")
    }
    return [f"--refresh-package={name}" for name in sorted(names)]


def run_isolated_test(
    package: Package,
    root: Path,
    *,
    release_dirs: tuple[Path, ...] = (),
    resolution: str = "highest",
) -> dict[str, str]:
    """Install the built wheel into a fresh venv and test the installed copy.

    One leg of the isolated validation; the caller runs it twice, the
    floor leg (``resolution="lowest-direct"``, every direct dependency
    at its declared floor, so a floor lying about compatibility fails
    here) and the latest leg (the world a fresh consumer gets).

    ``--find-links`` is limited to *release_dirs*, the co-released
    set's ``dist/`` directories: a leftover wheel from a non-released
    package must never mask that the release needs an unpublished
    dependency version. Everything else resolves from the repo's
    configured indexes, like a real consumer.
    Each distribution those directories carry is refreshed in uv's
    cache, so a member rebuilt at the same version installs the wheel
    just built and never an earlier build of the same filename.

    The toolchain installs after the wheel, at the lock's dev-group
    pins where a lock exists (bare pytest otherwise), and a probe
    then re-reads the package's own direct dependencies: the second
    install is pip-shaped and moves versions without erroring, so a
    toolchain pin that overlaps a floored dependency would silently
    undo the starvation. Movement is a taught refusal.

    Returns the resolved version per distribution, the report's raw
    material ("floor leg: livery-forge 0.1.0").
    """
    import json
    import tempfile

    # Sorted for determinism: with several wheels in dist a glob's
    # filesystem order once handed the leg a musllinux wheel the
    # venv could not install.
    wheels = sorted((package.directory / "dist").glob("*.whl"))
    if not wheels:
        fail(f"{package.name}: no wheel in dist/ to validate; build first")
    with tempfile.TemporaryDirectory() as scratch:
        from livery.workshop._pythons import venv_python

        venv = Path(scratch) / "venv"
        python = venv_python(venv)

        def _run_install(*args: str) -> None:
            result = toolroom.uv.opts(cwd=scratch, nofail=True, recorded=False)(*args)
            if result.code != 0:
                fail(
                    f"{package.name} isolated install ({resolution}) exited"
                    f" {result.code}:\n{result.stdout}{result.stderr}"
                )

        def _listing() -> dict[str, str]:
            listing = toolroom.uv.opts(cwd=scratch, nofail=True, recorded=False)(
                "pip", "list", "--python", str(python), "--format", "json"
            )
            versions: dict[str, str] = {}
            if listing.code == 0:
                for row in json.loads(listing.stdout or "[]"):
                    versions[str(row.get("name", ""))] = str(row.get("version", ""))
            return versions

        # The leg's own interpreter version, explicitly: bare `uv
        # venv` takes uv's default python, and a platform wheel
        # built for the running interpreter (cp311) cannot install
        # into a venv of another (cp314). Pure wheels never noticed.
        _run_install(
            "venv",
            "--python",
            f"{sys.version_info.major}.{sys.version_info.minor}",
            str(venv),
        )
        _run_install(
            "pip",
            "install",
            "--python",
            str(python),
            f"--resolution={resolution}",
            *[f"--find-links={d}" for d in release_dirs],
            *_refresh_args(release_dirs),
            *_index_args(root),
            _install_target(package, wheels[0]),
            # The declared dependencies ride the command line so
            # the resolution strategy treats them as direct; see
            # _direct_requirements.
            *_direct_requirements(package),
        )
        resolved = _listing()
        before = _direct_versions(package, resolved)
        # The toolchain never rides the starved install: lowest-direct
        # aimed at it once dragged pytest back a decade. Locked pins
        # where the workspace has them, minus what the leg resolved,
        # so the leg keeps its floor or its latest; pytest is a no-op
        # re-request when the pins already hold it.
        # The toolchain resolves from the same indexes as the wheel:
        # the lock's pins name whatever versions the workspace's own
        # index serves, and a bare-PyPI install cannot see those.
        pins = _dev_pins(root, Path(scratch))
        if pins is not None:
            pins = _pins_without(pins, resolved)
            _run_install(
                "pip",
                "install",
                "--python",
                str(python),
                *_index_args(root),
                "-r",
                str(pins),
            )
        _run_install(
            "pip", "install", "--python", str(python), *_index_args(root), "pytest"
        )
        after = _direct_versions(package, _listing())
        moved = {
            name: (before[name], version)
            for name, version in after.items()
            if name in before and before[name] != version
        }
        if moved:
            listed = ", ".join(
                f"{name} {was} -> {now}" for name, (was, now) in sorted(moved.items())
            )
            fail(
                f"{package.name}: the toolchain install moved direct"
                f" dependencies the {resolution} leg had resolved: {listed}."
                " The starvation must stay honest: a toolchain tool needs"
                " another version of a dependency the member declares, so"
                " pin that tool where the two agree, or widen the member's"
                " floor."
            )
        tests = package.directory / "tests"
        if tests.is_dir():
            result = footman.run(
                [
                    str(python),
                    "-m",
                    "pytest",
                    str(tests),
                    "-q",
                    "-p",
                    "no:cacheprovider",
                    # pytest derives its rootdir from the test paths and
                    # finds the workspace pyproject, whose addopts would
                    # hand the leg xdist workers and coverage flags; the
                    # leg is serial and unmetered by design.
                    "-o",
                    "addopts=",
                ],
                cwd=scratch,
                env=_leg_env(root, venv),
                nofail=True,
                recorded=False,
            )
            if result.code != 0:
                fail(
                    f"{package.name} isolated tests ({resolution}) failed:\n"
                    f"{result.stdout[-4000:]}{result.stderr[-2000:]}"
                )
        return _listing()
