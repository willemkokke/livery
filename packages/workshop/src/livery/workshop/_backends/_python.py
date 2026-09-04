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
import sys
import tempfile
import tomllib
from pathlib import Path

import footman
import toolroom
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
    contract = tomllib.loads((package.directory / "workshop.toml").read_text("utf-8"))
    value = (contract.get("qa") or {}).get("coverage_floor")
    return float(value) if value is not None else None


def measured_coverage(root: Path, packages: tuple[Package, ...]) -> dict[str, float]:
    """Per-package line coverage from the run's ``.coverage`` data."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as handle:
        report = handle.name
    result = toolroom.coverage.opts(cwd=root)("json", "-o", report)
    if result.code != 0:
        fail(f"coverage json exited {result.code}:\n{result.stdout}{result.stderr}")
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
            + "\n  raise the code, or lower a floor deliberately in workshop.toml"
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
    if os.environ.get("COVERAGE_PROCESS_START"):
        # Coverage's own subprocess variable: when it is set, every
        # python this venv starts is already metered from interpreter
        # start (the CI gate arms it), so the run adds no second
        # meter and the enforcement happens once, on the merged
        # union, in the aggregating job.
        pytest.opts(in_process=False)(*dirs, *pytest_args)
        return
    # Bare --cov: the measured source is [tool.coverage.run] source,
    # the namespace the render derived, never a spelled module.
    pytest.opts(in_process=False)(*dirs, "--cov", "--cov-report=", *pytest_args)
    report_coverage(root, packages)


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
        venv = Path(scratch) / "venv"
        python = venv / "bin" / "python"

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
            *_index_args(root),
            str(wheels[0]),
            # The declared dependencies ride the command line so
            # the resolution strategy treats them as direct; see
            # _direct_requirements.
            *_direct_requirements(package),
        )
        before = _direct_versions(package, _listing())
        # The toolchain never rides the starved install: lowest-direct
        # aimed at it once dragged pytest back a decade. Locked pins
        # where the workspace has them; pytest is a no-op re-request
        # when the pins already hold it.
        pins = _dev_pins(root, Path(scratch))
        if pins is not None:
            _run_install("pip", "install", "--python", str(python), "-r", str(pins))
        _run_install("pip", "install", "--python", str(python), "pytest")
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
                " The starvation must stay honest; align the dev-group pin"
                " with the floor, or release the dependency first."
            )
        tests = package.directory / "tests"
        if tests.is_dir():
            result = footman.run(
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
                nofail=True,
                recorded=False,
            )
            if result.code != 0:
                fail(
                    f"{package.name} isolated tests ({resolution}) failed:\n"
                    f"{result.stdout[-4000:]}{result.stderr[-2000:]}"
                )
        return _listing()
