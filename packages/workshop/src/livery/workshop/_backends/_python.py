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
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import livery.footman.api as footman
import livery.toolroom.tools.api as tools
from livery.footman.api import fail
from livery.toolroom.tools.api import (
    basedpyright,
    mypy,
    pyrefly,
    pytest,
    ruff,
    ruff_format,
    ty,
)
from livery.workshop._contract import load_contract
from livery.workshop._kinds import Extractor
from livery.workshop._packages import Neighbours, Package
from livery.workshop._state import RunContext, slug

if TYPE_CHECKING:
    from livery.workshop._coverage_store import Record
    from livery.workshop._git_ops import GitOps
    from livery.workshop._registries import RegistryTarget

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


#: The python suffixes ruff owns, what a python check's claims admit;
#: a foreign file an explicit path names passes through untouched.
PY_SUFFIXES = (".py", ".pyi")

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
        if not path.is_file() or path.suffix in PY_SUFFIXES:
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


def run_typecheck(paths: tuple[str, ...] = (), only: str = "") -> None:
    """Type-check with the four gating checkers in parallel, or with *only* one.

    basedpyright runs with warnings gating as errors. mypy is strict
    on livery.* and checks every test body as consumer code, once per
    platform (linux from config, darwin and win32 by flag), since
    mypy has no all-platforms mode. ty and pyrefly check every
    platform at once at the scopes pyproject pins. All four gate: a
    checker livery uses is a checker the tree is clean against.

    *paths* narrows basedpyright and mypy to the affected subset; ty
    and pyrefly keep their configured whole either way. *only* names
    one checker, ``basedpyright``, ``mypy``, ``ty`` or ``pyrefly``,
    the way each is a check of the typecheck role.
    """
    from livery.footman.api import parallel, step

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

    steps = {
        "basedpyright": (step(based, title="basedpyright"),),
        "mypy": (step(mypy_linux), step(mypy_darwin), step(mypy_win32)),
        "ty": (step(run_ty, title="ty"),),
        "pyrefly": (step(run_pyrefly, title="pyrefly"),),
    }
    chosen = [
        s for name, group in steps.items() if not only or name == only for s in group
    ]
    parallel(*(made() for made in chosen))


def run_typecomplete(packages: tuple[Package, ...]) -> None:
    """Verify each package's public API is 100% type-complete.

    Each of the package's roots ([livery.workshop._backends._python.module_roots][])
    is verified, through its ``api`` module where the root is a
    namespace; a package with no src tree is verified under the module
    its distribution name spells. The exit code is the verdict, 0 only
    when every public symbol has a fully known type.
    """
    for package in packages:
        src = package.directory / "src"
        for root in module_roots(package) or (module_for(package),):
            api = src.joinpath(*root.split("."), "api.py")
            target = f"{root}.api" if api.is_file() else root
            basedpyright(verifytypes=target, ignoreexternal=True)


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

    def homes(self) -> list[Path]:
        """``pyproject.toml``, and every module that may carry ``__version__``.

        That is a namespace root's ``api.py`` or a regular package's
        ``__init__.py``.
        """
        src = self._package.directory / "src"
        modules = (
            [*src.rglob("__init__.py"), *src.rglob("api.py")] if src.is_dir() else []
        )
        return [self._package.directory / "pyproject.toml", *sorted(modules)]

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
        for init in self.homes()[1:]:
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
    # The contract's judge holds the epsilon to a number.
    epsilon = float(qa.get("coverage-epsilon", DEFAULT_EPSILON))
    if epsilon < 0:
        fail(f"{package.path}: [qa] coverage-epsilon must not be negative")
    if raw == AUTO_RATCHET:
        return FloorPolicy(None, True, epsilon)
    if isinstance(raw, str):
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


#: What coverage.py says when the run left no data: a selection of
#: tests that reached no source measures nothing, which a preview
#: reports and a judged leg refuses.
NO_DATA = "No data to report"


def measured_coverage(
    root: Path, packages: tuple[Package, ...], *, none_ok: bool = False
) -> dict[str, float]:
    """Per-package coverage from the run's ``.coverage`` data, statements and branches.

    A python package's percentage is the statements and branches of
    its files the data reached, over all of them, the figure
    coverage.py reports as a file's total; a package with none to
    reach is 100. A native package's is its lines part's, the lines
    hit over the instrumentable lines of its sources, and a native
    package with no part left is absent, never a number. A run that
    left no python data (`NO_DATA`) is empty under *none_ok* and a
    refusal otherwise.
    """
    from livery.workshop._coverage_lines import percent, read_parts

    lines_parts = read_parts(root)
    measured: dict[str, float] = {}
    for package in packages:
        if measures_lines(package) and package.path in lines_parts:
            reached = percent(lines_parts[package.path], package)
            measured[package.path] = 100.0 if reached is None else reached
    arcs_packages = tuple(p for p in packages if not measures_lines(p))
    if not arcs_packages:
        return measured
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
    # nofail: the exit code is read here, so a run that left no data
    # is told apart from a broken read instead of ending the gate.
    result = tools.coverage.opts(cwd=root, env=scrubbed, nofail=True)(
        "json", "-o", report
    )
    if result.code != 0:
        if none_ok and NO_DATA in result.stdout + result.stderr:
            return measured
        fail(f"coverage json exited {result.code}:\n{result.stdout}{result.stderr}")
    data = json.loads(Path(report).read_text("utf-8"))
    Path(report).unlink(missing_ok=True)
    totals: dict[str, list[int]] = {package.path: [0, 0] for package in arcs_packages}
    for filename, entry in data.get("files", {}).items():
        for package in arcs_packages:
            if filename.startswith(f"{package.path}/src/"):
                summary = entry.get("summary", {})
                totals[package.path][0] += int(summary.get("covered_lines", 0)) + int(
                    summary.get("covered_branches", 0)
                )
                totals[package.path][1] += int(summary.get("num_statements", 0)) + int(
                    summary.get("num_branches", 0)
                )
                break
    measured.update(
        {
            path: (100.0 * covered / statements if statements else 100.0)
            for path, (covered, statements) in totals.items()
        }
    )
    return measured


def report_coverage(root: Path, packages: tuple[Package, ...]) -> None:
    """Print each package's local coverage beside its floor, no verdict.

    One machine's run misses the platform branches and the task
    shells only the measured CI union reaches, so the local number is
    a low-biased preview: it informs, and the aggregating CI job's
    union is what the floors gate.
    """
    from livery.workshop._coverage_store import WORKSPACE_TESTS

    measured = measured_coverage(root, packages, none_ok=True)
    if not measured:
        print(
            "  coverage: nothing measured by this run (its tests reached no"
            " source); the CI union judges the floors"
        )
    arcs_measured = any(
        not measures_lines(package) and package.path in measured for package in packages
    )
    for package in packages:
        if package.path == WORKSPACE_TESTS:
            continue  # a unit of the union, never a package with a floor
        policy = coverage_policy(package)
        if policy is None:
            continue
        if measures_lines(package):
            if package.path not in measured:
                print(
                    f"  coverage {package.path}: not measured here (its tests did"
                    " not run, or ran without their measurer)"
                )
                continue
        elif not arcs_measured:
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


#: coverage.py's reading, the measurer of every python suite and the
#: workspace's own tests.
ARCS_MEASURER = "arcs"


def measures_lines(package: Package) -> bool:
    """Whether *package*'s suite is measured as lines: a native kind's, no python one's.

    The workspace's own tests carry no kind and are python's.
    """
    from livery.workshop._kinds import is_python_kind, kind_names

    return package.kind in kind_names() and not is_python_kind(package.kind)


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

    from livery.workshop._coverage_lines import pairs, read_parts
    from livery.workshop._coverage_store import LINES, Unit, closure_id
    from livery.workshop._git_ops import GitOps
    from livery.workshop._state import run_context

    leg = marker["leg"]
    parts = sorted(root.glob(".coverage.*"))
    lines_parts = read_parts(root)
    if not parts and not lines_parts:
        return
    if parts:
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
        measurer = LINES if measures_lines(package) else ARCS_MEASURER
        if measurer == LINES:
            lines = lines_parts.get(package.path)
            if lines is None:
                print(
                    f"  coverage store: {package.path} ran without a measurement;"
                    " nothing to store for it"
                )
                continue
            files = pairs(lines)
        elif parts:
            files = suite_arcs_by_context(
                root / SUITES_DATA, packages, package, root=root
            )
        else:
            continue
        if run is None:
            print(
                f"  coverage store: {package.path} measured ({len(files)} files);"
                " outside CI nothing is stored"
            )
            continue
        key = closure_id(git, packages, package)
        measured[package.path] = Unit(
            package.path, key, run.run_id, git.head_sha(), files, measurer=measurer
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
    from livery.workshop._coverage_lines import read_parts
    from livery.workshop._verified import NOTHING, VERIFIED, read_marker

    parts = sorted(root.glob(".coverage.*"))
    marker = read_marker(root)
    scope = marker["scope"]
    if timing is not None:
        timing = {**timing, "scope": marker}
    if not parts and not read_parts(root) and not (root / ".coverage").is_file():
        if scope in (VERIFIED, NOTHING):
            # A skipped leg measured nothing and names no unit, so the
            # union carries every unit from the records.
            print(f"  coverage: no data, the gate ran {scope!r}; nothing to combine")
            _put_leg(root, marker, {}, timing=timing)
            return
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
        fail(
            "this leg left no coverage data: nothing was metered. Inside CI"
            " the test runner arms COVERAGE_PROCESS_START in pytest's"
            " environment, so the tests and every process they start are"
            " metered; a leg that ran its gate and left no data ran no"
            " metered pytest, and there is nothing to union."
        )
    store_suites(root, packages, marker=marker, timing=timing)
    if parts:
        result = tools.coverage.opts(
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
    from livery.workshop._coverage_lines import (
        from_pairs,
        merge,
        remove_parts,
        write_part,
    )
    from livery.workshop._coverage_store import (
        LINES,
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
    # The lines units, merged per package across the legs and the
    # records; written as parts once every leg is read.
    lines_by_package: dict[str, dict[str, dict[int, int]]] = {}
    lines_reused = 0
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
                if unit.measurer == LINES:
                    lines_by_package[path] = merge(
                        lines_by_package.get(path, {}), from_pairs(unit.files)
                    )
                    continue
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
            kept = ""
            for base in bases:
                record = _read_record(root, held, base, leg.label)
                row = record.at(suite.path, key)
                current = record.units.get(suite.path)
                if row is not None:
                    carried[suite.path] = (base, row)
                    if row is not current:
                        # The leg read the current row; a write since
                        # moved it on and kept it at this closure.
                        kept = ", a row kept after its record moved on"
                    break
                states.append(
                    f"{base} only at {current.closure[:12]}"
                    if current is not None
                    else f"{base} none"
                )
            else:
                fail(
                    f"leg {leg.label}: no record holds {suite.path} at closure"
                    f" {key[:12]} ({', '.join(states)}). A leg skips a suite"
                    " only when a record holds it at the suite's closure, and"
                    " a record keeps a row it moved on from for a day, so this"
                    " leg narrowed without the records, or the run outlived"
                    " that day; a run of the full gate measures it again."
                )
            base, row = carried[suite.path]
            if row.measurer == LINES:
                lines_by_package[suite.path] = merge(
                    lines_by_package.get(suite.path, {}), from_pairs(row.files)
                )
                lines_reused += 1
            else:
                reused.append(
                    _write_unit(
                        scratch / leg.label,
                        f"reuse-{slug(suite.path)}.coverage",
                        row.files,
                    )
                )
            print(
                f"  coverage: {suite.path} on {leg.label}: reused from run"
                f" {row.run} ({len(row.files)} files), {base}'s record{kept}"
            )
        if target:
            own = _read_record(root, held, target, leg.label)
            writes.append((leg.label, dict(leg.units), carried, own))
    if not collected and not reused and not lines_by_package:
        print("  coverage: no unit to union; nothing judged")
        return ()
    scrubbed = _unmetered()
    if collected or reused:
        result = tools.coverage.opts(
            cwd=root, env=scrubbed, nofail=True, recorded=False
        )("combine", *(str(path) for path in collected + reused))
        if result.code != 0:
            fail(
                f"coverage combine exited {result.code}:\n"
                f"{result.stdout}{result.stderr}"
            )
        report = tools.coverage.opts(
            cwd=root, env=scrubbed, nofail=True, recorded=False
        )("report", "--sort=cover")
        print(report.stdout.rstrip())
    remove_parts(root)
    for path, lines in sorted(lines_by_package.items()):
        write_part(root, path, lines)
    if lines_by_package:
        print(f"  coverage: {len(lines_by_package)} native package(s) unioned by lines")
    print(
        f"  coverage: the union of {ran} leg(s) and {len(reused) + lines_reused}"
        " reused suite(s)"
    )
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
        why = put_record(
            root, run, leg=label, fresh=rows, remove=stale, base=target, held=own
        )
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


def render_coverage_pages(root: Path, packages: tuple[Package, ...]) -> list[str]:
    """Render an htmlcov tree for each of *packages* from the measured data.

    The python kind's coverage pages: the site's extension hands over the
    packages that declare an ``htmlcov`` report, and each gets its
    tree rendered from the workspace's measured data, scoped to its
    own files. In
    the merge point's deploy job the data is main's coverage record,
    every unit on every check leg, the union main's gate judged
    whatever its legs ran; anywhere else it is the local ``.coverage``
    a gate run left. With none, nothing renders and the coverage page
    states the absence.
    """
    import tempfile

    from livery.workshop._state import run_context

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
        name = package.directory.name
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
    """In the merge point's deploy job, main's recorded units for every check leg.

    Returns the files and the misses. Anywhere else nothing is pulled:
    a pull request's docs job builds without the legs' data by design,
    and a machine's build renders the local data a gate run left, or
    states the absence.
    """
    import os

    from livery.workshop._points import check_legs
    from livery.workshop._state import LEG_VARIABLE, POINT_VARIABLE, run_context

    deploying = (
        os.environ.get(POINT_VARIABLE) == "merge"
        and os.environ.get(LEG_VARIABLE) == "deploy"
    )
    if run_context() is None or not deploying:
        return [], []
    from livery.workshop._state import remote_snapshot

    with remote_snapshot(root, fetch=("coverage/main/",)):
        return stored_union(root, check_legs(root), root / "coverage-data")


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
        if package.path not in measured and measures_lines(package):
            print(f"  coverage {package.path}: not measured")
            problems.append(
                f"{package.path}: not measured; its tests ran without their"
                " measurer, or never ran, and an unmeasured suite never passes"
            )
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


def gate_build(package: Package, root: Path) -> None:
    """Nothing: python tests run on source, so there is nothing to build."""
    del package, root


def test(
    package: Package,
    root: Path,
    *,
    selection: tuple[str, ...] = (),
) -> None:
    """Run *package*'s tests: its suite, or the files in *selection* alone."""
    files = tuple(f"{package.path}/{path}" for path in selection)
    run_test(
        packages=(package,),
        root=root,
        scoped=True,
        selection={package.path: files} if files else None,
    )


def examples_of(package: Package) -> list[Path]:
    """The example files *package* ships under ``docs/examples/``, sorted.

    A ``conftest.py`` there is the package's setup around them, never
    an example.
    """
    base = package.directory / "docs" / "examples"
    if not base.is_dir():
        return []
    return sorted(path for path in base.rglob("*.py") if path.name != "conftest.py")


def run_examples(package: Package, root: Path, files: tuple[str, ...] = ()) -> None:
    """Run *package*'s documentation examples, one test per file.

    Pytest over the package's ``docs/examples/`` directory, or over
    *files* alone when every one of them is an example, whose files
    the workshop's examples plugin collects
    ([livery.workshop._pytest_examples][]), from the workspace root so
    the workspace's pytest configuration applies. A named file that is
    no example, the examples' ``conftest.py``, is every example's
    setup, so the whole directory runs. Captured and printed on a red
    exit, so a runner's log carries the failing example's file and
    line; a package without examples says so and passes.
    """
    from livery.workshop._pytest_examples import is_example

    if not examples_of(package):
        print(f"  examples: {package.path} has none")
        return
    named = tuple(name for name in files if is_example(Path(name)))
    targets = (
        named
        if files and len(named) == len(files)
        else (f"{package.path}/docs/examples",)
    )
    result = pytest.opts(in_process=False, cwd=root, nofail=True)(*targets)
    if result.code != 0:
        print(result.stdout, end="")
        print(result.stderr, end="")
        fail(f"examples of {package.path}: pytest exited {result.code}")


#: The variables that tell a test it runs on a forge's runner. A
#: machine's gate sets them, so a test that reads them fails here the
#: way it would fail on the runner, instead of one push later.
RUNNER_VARIABLES = ("CI", "GITHUB_ACTIONS")


def runner_shaped(env: Mapping[str, str]) -> dict[str, str]:
    """*env* with the runner's variables set, as the check legs see the suite."""
    return {**env, **dict.fromkeys(RUNNER_VARIABLES, "true")}


def run_test(
    *pytest_args: str,
    packages: tuple[Package, ...] = (),
    root: Path | None = None,
    scoped: bool = False,
    selection: Mapping[str, tuple[str, ...]] | None = None,
) -> None:
    """Run the test suite; *pytest_args* forwarded verbatim.

    With *packages* and *root*, the run measures coverage over
    ``livery``: inside CI the tests run metered for the gate job's
    union, and on a machine the run prints each package's number
    beside its floor. *scoped* additionally narrows collection to
    those packages' own test directories (the affected mode), and
    *selection* names, per package path, the test files that stand
    for the package's directory. A machine's run sets the runner's
    variables (`RUNNER_VARIABLES`), so the suite is judged the way
    the legs judge it. Without *packages* the arguments pass through
    untouched.
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

        chosen = selection or {}
        picked: list[str] = []
        for package in packages:
            if package.path == WORKSPACE_TESTS:
                continue
            if (package.directory / "tests").is_dir():
                picked.extend(chosen.get(package.path) or (f"{package.path}/tests",))
        if (root / WORKSPACE_TESTS).is_dir():
            picked.extend(chosen.get(WORKSPACE_TESTS) or (WORKSPACE_TESTS,))
        dirs = tuple(picked)
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
                # once, on the union, in the gate job. Captured, and
                # printed on a red exit: the job's log is the run's
                # evidence and nothing else in a runner shows a red
                # test's words. Captured rather than streamed because a
                # captured child gets its own hidden console on Windows,
                # where a streamed one shares the runner's and a control
                # event a worker raises would interrupt the runner too.
                armed = {**env, ARMED: str(root / "pyproject.toml")}
                result = pytest.opts(in_process=False, env=armed, nofail=True)(
                    *dirs, *pytest_args
                )
                if result.code != 0:
                    print(result.stdout, end="")
                    print(result.stderr, end="")
                    fail(f"pytest exited {result.code}")
            else:
                # Bare --cov: the measured source is [tool.coverage.run]
                # source, the namespace the render derived, never a
                # spelled module. The runner's variables are set, so
                # a test that reads them is judged here as on the leg.
                pytest.opts(in_process=False, env=runner_shaped(env))(
                    *dirs, "--cov", "--cov-report=", *pytest_args
                )
        finally:
            for line in speed_lines(root, sums, leg=run.leg if run else ""):
                print(line)
    if run is None:
        report_coverage(root, packages)


def speed_lines(root: Path, sums: Path, *, leg: str = "") -> list[str]:
    """Each package's summed test time; on a check leg, beside its mark there.

    *sums* is the file the speed plugin wrote; no file means the
    plugin did not run. With *leg* the run is a check leg's, and each
    line carries the package's mark on that leg or says there is
    none; an unreadable store says so once and the times still print.
    Without *leg* the run is a machine's: the times print alone,
    since the marks are judged on the CI legs and a machine's clock
    is not one of them, and nothing here reads the store.
    """
    from livery.workshop import _speed

    if not sums.is_file():
        return []
    try:
        loaded = json.loads(sums.read_text("utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(loaded, dict) or not loaded:
        return []
    current: dict[tuple[str, str], _speed.Mark] | None = None
    lines: list[str] = []
    if leg:
        current, why = _speed.marks(root)
        if current is None:
            lines.append(f"  speed marks: not read ({why})")
    for package, data in sorted(loaded.items()):
        if not isinstance(data, dict):
            continue
        seconds = float(data.get("seconds", 0.0))
        tests = int(data.get("tests", 0))
        if not leg:
            beside = "judged on the CI legs"
        elif current is None:
            beside = "mark unknown"
        else:
            mark = current.get((str(package), leg))
            beside = (
                f"mark {mark.seconds:.1f}s on {leg}"
                if mark is not None
                else f"no mark on {leg}"
            )
        lines.append(f"  speed {package}: {seconds:.1f}s over {tests} tests ({beside})")
    return lines


def module_roots(package: Package) -> tuple[str, ...]:
    """The import prefixes *package* owns, read from its src tree.

    A prefix is the topmost directory under ``src`` holding an
    ``api.py`` (a namespace root) or an ``__init__.py`` (a regular
    package) on its branch. Read from the tree rather than
    derived from the distribution name: three distributions share the
    ``livery.toolroom`` namespace, so a transformed name would
    attribute two of them to the third. A package with no python
    source owns nothing.
    """
    src = package.directory / "src"
    if not src.is_dir():
        return ()
    roots: list[str] = []
    from livery.workshop._packages import root_marks

    for init in root_marks(src):
        dotted = ".".join(init.relative_to(src).parts[:-1])
        if not dotted:
            continue
        if any(dotted == root or dotted.startswith(root + ".") for root in roots):
            continue
        roots.append(dotted)
    return tuple(sorted(roots))


def plugin_modules(package: Package) -> tuple[str, ...]:
    """The modules *package* declares as footman task entry points.

    A module the runner loads as a plugin has the runner present by
    construction, and whatever the extension stack mounts with it, so its
    imports of either are not dependencies of the distribution. The
    fact lives in the package's own metadata; nothing here needs to
    be told a second time. An entry naming a namespace root's ``api``
    stands for the root.
    """
    pyproject = package.directory / "pyproject.toml"
    if not pyproject.is_file():
        return ()
    data = tomllib.loads(pyproject.read_text("utf-8"))
    groups = data.get("project", {}).get("entry-points", {})
    modules = []
    for group in ("footman.tasks", "footman.builtin"):
        for target in (groups.get(group) or {}).values():
            module = str(target).partition(":")[0]
            # A root's api module is the root's face: a plugin named
            # there is the whole root, as a regular package's root was.
            modules.append(module.removesuffix(".api"))
    return tuple(modules)


def _extra_distributions(package: Package) -> frozenset[str]:
    """The distribution names *package* declares in its optional extras.

    An extra is a declaration: the integration needs that package,
    and nothing installs it by default. A reference it covers is
    accounted for, and no ``[[depends]]`` edge follows, since the
    reverse check reads ``[project.dependencies]`` alone.
    """
    pyproject = package.directory / "pyproject.toml"
    if not pyproject.is_file():
        return frozenset()
    data = tomllib.loads(pyproject.read_text("utf-8"))
    found = set()
    for requirements in (
        data.get("project", {}).get("optional-dependencies") or {}
    ).values():
        for requirement in requirements:
            name = str(requirement)
            for cut in "[>=<!~; ":
                name = name.partition(cut)[0]
            found.add(name)
    return frozenset(found)


def _references_in(source: Path) -> list[str]:
    """The dotted names *source* imports, guarded ones left out.

    Read through the layering check's one parse, so a file another
    rule also reads is parsed once per gate; a file that does not
    parse contributes nothing, since the syntax gate names it.
    """
    from livery.workshop._ast_rules import parsed_source

    parsed = parsed_source(source)
    return [] if parsed is None else list(parsed.imports)


def referenced_siblings(package: Package, around: Neighbours) -> dict[str, str]:
    """Which siblings *package* uses and has not accounted for, by area.

    The area is ``src`` or ``tests``, which is what decides a runtime
    edge from a test one: the import site answers the kind, so nothing
    has to guess it. Three of this kind's own conventions account for
    a reference without an edge, and it is left out here: an import
    inside a module the package declares as a footman task entry
    point, a sibling named in one of its optional extras, and an
    import guarded by ``try``/``except ImportError``.
    """
    exempt_dists = _extra_distributions(package)
    plugins = plugin_modules(package)
    found: dict[str, str] = {}
    for area in ("tests", "src"):
        base = package.directory / area
        if not base.is_dir():
            continue
        for source in sorted(base.rglob("*.py")):
            inside_plugin = False
            if area == "src":
                dotted = ".".join(source.relative_to(base).parts)
                dotted = dotted.removesuffix(".py").removesuffix(".__init__")
                inside_plugin = any(
                    dotted == module or dotted.startswith(module + ".")
                    for module in plugins
                )
            for reference in _references_in(source):
                owner = around.owner_of(reference)
                if not owner or owner == package.path:
                    continue
                if inside_plugin or around.by_path[owner].name in exempt_dists:
                    continue
                found[owner] = area
    found.pop(package.path, None)
    return found


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


def declare_requirement(package: Package, dependency: Package, floor: str) -> list[str]:
    """Add ``<dependency>>=<floor>`` to the project's dependencies; the files changed.

    A text edit that keeps the manifest's formatting: the entry joins
    the existing list, one per line, or opens the list when the
    project declares none. Already declared, whatever the constraint,
    means nothing to write. A manifest without a ``[project]`` table
    refuses naming it.
    """
    import re as _re

    pyproject = package.directory / "pyproject.toml"
    if dependency.name in declared_requirements(package):
        return []
    text = pyproject.read_text("utf-8")
    entry = f'    "{dependency.name}>={floor}",\n'
    listed = _re.search(r"^dependencies = \[(.*?)^\]", text, flags=_re.M | _re.S)
    inline = _re.search(r"^dependencies = \[(.*)\]$", text, flags=_re.M)
    if listed is not None:
        head, tail = text[: listed.end() - 1], text[listed.end() - 1 :]
        text = head + entry + tail
    elif inline is not None:
        items = [item.strip() for item in inline.group(1).split(",") if item.strip()]
        lines = "".join(f"    {item},\n" for item in items) + entry
        text = (
            text[: inline.start()]
            + f"dependencies = [\n{lines}]"
            + text[inline.end() :]
        )
    else:
        header = _re.search(r"^\[project\]\n", text, flags=_re.M)
        if header is None:
            fail(f"{pyproject} has no [project] table to declare a dependency in")
        text = (
            text[: header.end()]
            + f"dependencies = [\n{entry}]\n"
            + text[header.end() :]
        )
    pyproject.write_text(text, encoding="utf-8")
    return ["pyproject.toml"]


def build(package: Package, root: Path, *, epoch: int = 0) -> Path:
    """Build *package*'s wheel and sdist into its ``dist/``; the dist dir.

    Always from a clean ``dist/``: reusing an artifact from an
    earlier build installs stale code under current tests, a silent
    footgun bought off for seconds of build. *epoch* (a commit's
    timestamp) rides ``SOURCE_DATE_EPOCH`` so a pure-Python rebuild
    at the same ref is byte-identical.
    """
    import shutil

    from livery.workshop._docs_contract import materialise_module_docs

    # The wheel-embedded _docs refresh whole from packages/<name>/docs
    # here, so the wheel can never carry docs older than the tree it
    # was built from. Machine territory: gitignored, never hand-edited.
    materialise_module_docs(package)
    dist = package.directory / "dist"
    shutil.rmtree(dist, ignore_errors=True)
    env = dict(os.environ)
    if epoch:
        env["SOURCE_DATE_EPOCH"] = str(epoch)
    result = tools.uv.opts(cwd=package.directory, nofail=True, recorded=False, env=env)(
        "build", "--out-dir", str(dist)
    )
    if result.code != 0:
        fail(
            f"uv build ({package.name}) exited {result.code}:\n"
            f"{result.stdout}{result.stderr}"
        )
    if not list(dist.glob("*.whl")):
        fail(f"{package.name}: the build produced no wheel in {dist}")
    return dist


def publish_artifact(
    package: Package, root: Path, *, version: str, target: RegistryTarget
) -> bool:
    """Upload ``dist/*`` to the python index; the kind's publish seam.

    ``uv publish``, to the target's upload endpoint with its
    credential. *version* and *root* are other kinds' fields: the
    wheels in ``dist/`` already carry their versions, and the index
    needs nothing from the workspace.
    """
    from livery.workshop._publish import publish_wheels

    del root, version
    return publish_wheels(package, index_url=target.publish_url, token=target.token)


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
    result = tools.uv.opts(cwd=root, nofail=True, recorded=False)(
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
    wheels_dir: Path | None = None,
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

    *wheels_dir* names where the wheel to install is, for a leg that
    built one outside the collected ``dist/``; the package's own
    ``dist/`` otherwise.

    Returns the resolved version per distribution, the report's raw
    material ("floor leg: livery-forge 0.1.0").
    """
    import json
    import tempfile

    # Sorted for determinism: with several wheels in dist a glob's
    # filesystem order once handed the leg a musllinux wheel the
    # venv could not install.
    built = wheels_dir if wheels_dir is not None else package.directory / "dist"
    wheels = sorted(built.glob("*.whl"))
    if not wheels:
        fail(f"{package.name}: no wheel in {built} to validate; build first")
    with tempfile.TemporaryDirectory() as scratch:
        from livery.workshop._pythons import venv_python

        venv = Path(scratch) / "venv"
        python = venv_python(venv)

        def _run_install(*args: str) -> None:
            result = tools.uv.opts(cwd=scratch, nofail=True, recorded=False)(*args)
            if result.code != 0:
                fail(
                    f"{package.name} isolated install ({resolution}) exited"
                    f" {result.code}:\n{result.stdout}{result.stderr}"
                )

        def _listing() -> dict[str, str]:
            listing = tools.uv.opts(cwd=scratch, nofail=True, recorded=False)(
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


# --- the API reference ---------------------------------------------------------

#: The inventories cross-ecosystem references resolve against.
#: Pinned here so one workshop release moves every project; the
#: footman and toolroom pins retire when those repositories migrate
#: into the workspace.
INVENTORIES = (
    "https://docs.python.org/3/objects.inv",
    "https://willemkokke.github.io/footman/objects.inv",
    "https://willemkokke.github.io/toolroom/objects.inv",
)


def api_pages(package: Package) -> list[tuple[str, str]]:
    """(page path, dotted import path) per module, public first.

    Every module gets a page, underscore-private included: the
    standards fragment publishes a docstring the moment it is
    written. Public sorts before private at every level of the
    tree, and a package's ``__init__`` is its index page. A package
    may decline the whole reference with ``[docs] api = false``,
    the shape a forwarding shim takes: its surface is another
    package's, and documenting the forwarders would document the
    real thing twice.
    """
    from livery.workshop._docs_contract import declines_api, module_root

    if declines_api(package):
        return []
    root = module_root(package)
    if root is None:
        return []
    src = package.directory / "src"
    entries: list[tuple[tuple[tuple[bool, str], ...], str, str]] = []
    for path in root.rglob("*.py"):
        if path.name == "__main__.py" or "_docs" in path.parts:
            continue
        relative = path.relative_to(root).with_suffix("")
        parts = relative.parts
        if parts and parts[-1] == "__init__":
            parts = (*parts[:-1], "")
        key = tuple((part.startswith("_"), part) for part in parts)
        page = (
            "index.md"
            if relative.as_posix() == "__init__"
            else relative.with_suffix(".md")
            .as_posix()
            .replace("/__init__.md", "/index.md")
        )
        dotted = ".".join(path.relative_to(src).with_suffix("").parts)
        dotted = dotted.removesuffix(".__init__")
        entries.append((key, page, dotted))
    entries.sort()
    return [(page, dotted) for _key, page, dotted in entries]


def package_python_paths(package: Package) -> list[str]:
    """The search paths a package's ``[docs] python-paths`` hands the API renderer.

    Each is a directory relative to the package, holding modules the
    pages reference beyond the package's sources: rendered stubs, for
    one. Anything but a list of strings refuses naming the file.
    """
    contract_path = package.directory / "workshop.toml"
    table = load_contract(contract_path).get("docs") or {}
    if not isinstance(table, dict):
        return []
    declared = table.get("python-paths", [])
    if not isinstance(declared, list) or not all(
        isinstance(entry, str) for entry in declared
    ):
        fail(f"{contract_path}: [docs] python-paths must be a list of paths")
    return list(declared)


def api_sources(package: Package) -> list[str]:
    """The handler's search paths for *package*: its sources, then its declared ones."""
    name = package.directory.name
    found = [f"packages/{name}/src"] if api_pages(package) else []
    found += [f"packages/{name}/{extra}" for extra in package_python_paths(package)]
    return found


#: The mkdocstrings python handler's options. Google style is the house
#: convention; a docstring is published the moment it is written, empty
#: ones included.
PYTHON_HANDLER_OPTIONS: dict[str, object] = {
    "docstring_style": "google",
    "show_if_no_docstring": True,
    "show_root_heading": True,
    "show_root_full_path": True,
    "separate_signature": True,
    "show_signature_annotations": True,
    "signature_crossrefs": True,
    "members_order": "source",
    "merge_init_into_class": True,
    "summary": True,
    "heading_level": 2,
}


#: The Python kind's extractor: mkdocstrings' python handler over the
#: package's sources and its declared paths.
EXTRACTOR = Extractor(
    "python",
    pages=api_pages,
    sources=api_sources,
    options=PYTHON_HANDLER_OPTIONS,
    inventories=INVENTORIES,
)
