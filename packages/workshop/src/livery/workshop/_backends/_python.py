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
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

from footman import fail
from toolroom import basedpyright, mypy, pyrefly, pytest, ruff, ruff_format, ty

from livery.workshop._packages import Package

#: The whole repo, as CI lints it.
SRC = (".",)


def package_paths(packages: tuple[Package, ...]) -> tuple[str, ...]:
    """The src and tests directories the *packages* own, as they exist."""
    paths = []
    for package in packages:
        for name in ("src", "tests"):
            directory = package.directory / name
            if directory.is_dir():
                paths.append(f"{package.path}/{name}")
    return tuple(paths)


def run_format(check: bool = False, paths: tuple[str, ...] = SRC) -> None:
    """Format with ruff; *check* reports instead of rewriting."""
    ruff_format(*paths, check=check)


def run_lint(fix: bool = False, paths: tuple[str, ...] = SRC) -> None:
    """Lint with ruff; *fix* applies safe fixes in place."""
    ruff.check(*paths, fix=fix)


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
    from footman import parallel, step

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
        module = package.name.replace("-", ".")
        basedpyright(verifytypes=module, ignoreexternal=True)


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


def coverage_floor(package: Package) -> float | None:
    """The committed coverage floor from the package's contract, or None."""
    contract = tomllib.loads((package.directory / "livery.toml").read_text("utf-8"))
    value = (contract.get("qa") or {}).get("coverage_floor")
    return float(value) if value is not None else None


def measured_coverage(root: Path, packages: tuple[Package, ...]) -> dict[str, float]:
    """Per-package line coverage from the run's ``.coverage`` data."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as handle:
        report = handle.name
    result = subprocess.run(
        [sys.executable, "-m", "coverage", "json", "-o", report],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        fail(
            f"coverage json exited {result.returncode}:\n{result.stdout}{result.stderr}"
        )
    data = json.loads(Path(report).read_text("utf-8"))
    Path(report).unlink(missing_ok=True)
    totals: dict[str, list[int]] = {package.path: [0, 0] for package in packages}
    for filename, entry in data.get("files", {}).items():
        for package in packages:
            if filename.startswith(f"{package.path}/src/"):
                summary = entry.get("summary", {})
                totals[package.path][0] += int(summary.get("covered_lines", 0))
                totals[package.path][1] += int(summary.get("num_statements", 0))
                break
    return {
        path: (100.0 * covered / statements if statements else 100.0)
        for path, (covered, statements) in totals.items()
    }


#: How far below its floor a package may measure before the gate
#: fails. A refactor that deletes a few covered lines moves the
#: percentage by noise, and a hundredth of a percent is not a
#: coverage regression; the floor stays the declared high-water mark
#: and the grace absorbs the measurement jitter.
COVERAGE_GRACE = 0.5


def report_coverage(root: Path, packages: tuple[Package, ...]) -> None:
    """Print each package's local coverage beside its floor, no verdict.

    One machine's run misses the platform branches and the task
    shells only the measured CI union reaches, so the local number is
    a low-biased preview: it informs, and the aggregating CI job's
    union is what the floors gate.
    """
    measured = measured_coverage(root, packages)
    for package in packages:
        floor = coverage_floor(package)
        if floor is None:
            continue
        percent = measured.get(package.path, 0.0)
        print(
            f"  coverage {package.path}: {percent:.1f}% here"
            f" (floor {floor:.1f}% judges the CI union)"
        )


def enforce_coverage(root: Path, packages: tuple[Package, ...]) -> None:
    """Fail any package measurably below its committed floor.

    Prints each verdict with the floor and the grace, so the numbers
    on screen are the numbers enforced.
    """
    measured = measured_coverage(root, packages)
    problems = []
    for package in packages:
        floor = coverage_floor(package)
        if floor is None:
            continue
        percent = measured.get(package.path, 0.0)
        print(
            f"  coverage {package.path}: {percent:.1f}%"
            f" (floor {floor:.1f}%, grace {COVERAGE_GRACE}%)"
        )
        if percent < floor - COVERAGE_GRACE:
            problems.append(
                f"{package.path}: {percent:.1f}% is below the committed"
                f" floor of {floor:.1f}% by more than the {COVERAGE_GRACE}% grace"
            )
    if problems:
        fail(
            "coverage fell below the high-water marks:\n  "
            + "\n  ".join(problems)
            + "\n  raise the code, or lower a floor deliberately in livery.toml"
        )


def run_test(
    *pytest_args: str,
    packages: tuple[Package, ...] = (),
    root: Path | None = None,
    scoped: bool = False,
) -> None:
    """Run the test suite; *pytest_args* forwarded verbatim.

    With *packages* and *root*, the run measures coverage over
    ``livery`` and enforces each package's committed floor afterwards;
    *scoped* additionally narrows collection to those packages' own
    test directories (the affected mode). Without them the arguments
    pass through untouched.
    """
    if not packages or root is None:
        pytest.opts(in_process=False)(*pytest_args)
        return
    dirs: tuple[str, ...] = ()
    if scoped:
        dirs = tuple(
            f"{package.path}/tests"
            for package in packages
            if (package.directory / "tests").is_dir()
        )
    if os.environ.get("LIVERY_COVERAGE_PARENT") == "1":
        # A parent `coverage run` is measuring the whole fm invocation
        # (the CI gate), so the run adds no second meter and the
        # enforcement happens once, on the merged union, in the
        # aggregating job.
        pytest.opts(in_process=False)(*dirs, *pytest_args)
        return
    pytest.opts(in_process=False)(*dirs, "--cov=livery", "--cov-report=", *pytest_args)
    report_coverage(root, packages)


def build(package: Package, root: Path, *, epoch: int = 0) -> Path:
    """Build *package*'s wheel and sdist into its ``dist/``; the dist dir.

    Always from a clean ``dist/``: reusing an artifact from an
    earlier build installs stale code under current tests, a silent
    footgun bought off for seconds of build. *epoch* (a commit's
    timestamp) rides ``SOURCE_DATE_EPOCH`` so a pure-Python rebuild
    at the same ref is byte-identical.
    """
    import shutil

    dist = package.directory / "dist"
    shutil.rmtree(dist, ignore_errors=True)
    env = dict(os.environ)
    if epoch:
        env["SOURCE_DATE_EPOCH"] = str(epoch)
    result = subprocess.run(
        ["uv", "build", "--out-dir", str(dist)],
        cwd=package.directory,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    if result.returncode != 0:
        fail(
            f"uv build ({package.name}) exited {result.returncode}:\n"
            f"{result.stdout}{result.stderr}"
        )
    if not list(dist.glob("*.whl")):
        fail(f"{package.name}: the build produced no wheel in {dist}")
    return dist


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

    Returns the resolved version per distribution, the report's raw
    material ("floor leg: livery-forge 0.1.0").
    """
    import json
    import tempfile

    wheels = list((package.directory / "dist").glob("*.whl"))
    if not wheels:
        fail(f"{package.name}: no wheel in dist/ to validate; build first")
    with tempfile.TemporaryDirectory() as scratch:
        venv = Path(scratch) / "venv"
        for command in (
            ["uv", "venv", str(venv)],
            [
                "uv",
                "pip",
                "install",
                "--python",
                str(venv / "bin" / "python"),
                f"--resolution={resolution}",
                *[f"--find-links={d}" for d in release_dirs],
                *_index_args(root),
                str(wheels[0]),
            ],
            # The runner installs at the default resolution on purpose:
            # the floor leg's lowest-direct must starve the package's
            # own dependencies, never drag pytest back a decade.
            [
                "uv",
                "pip",
                "install",
                "--python",
                str(venv / "bin" / "python"),
                "pytest",
            ],
        ):
            result = subprocess.run(
                command, cwd=scratch, capture_output=True, text=True, check=False
            )
            if result.returncode != 0:
                fail(
                    f"{package.name} isolated install ({resolution}) exited"
                    f" {result.returncode}:\n{result.stdout}{result.stderr}"
                )
        tests = package.directory / "tests"
        if tests.is_dir():
            result = subprocess.run(
                [
                    str(venv / "bin" / "python"),
                    "-m",
                    "pytest",
                    str(tests),
                    "-q",
                    "-p",
                    "no:cacheprovider",
                ],
                cwd=scratch,
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                fail(
                    f"{package.name} isolated tests ({resolution}) failed:\n"
                    f"{result.stdout[-4000:]}{result.stderr[-2000:]}"
                )
        listing = subprocess.run(
            [
                "uv",
                "pip",
                "list",
                "--python",
                str(venv / "bin" / "python"),
                "--format",
                "json",
            ],
            cwd=scratch,
            capture_output=True,
            text=True,
            check=False,
        )
        resolved: dict[str, str] = {}
        if listing.returncode == 0:
            for row in json.loads(listing.stdout or "[]"):
                resolved[str(row.get("name", ""))] = str(row.get("version", ""))
        return resolved
