"""The python package kind: build, version, publish and test a python distribution.

The base registers the python kind with this module as its backend, so
every package of kind ``python`` builds, stamps, publishes and runs its
tests through the functions here. One invocation covers every python
package at once: the checkers read their scopes from the workspace's
own configuration, so the whole repository is checked as CI checks it,
and a tracked file outside any package still cannot pass the gate and
fail the build.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import tomllib
from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

import livery.footman as footman
import livery.toolroom.tools as tools
from livery.footman import fail
from livery.toolroom.tools import pytest
from livery.workshop import Query
from livery.workshop._packages import Neighbours, Package, SourceReferences

if TYPE_CHECKING:
    from livery.workshop._registries import RegistryTarget

#: The python suffixes a python step's claims admit; a foreign file
#: an explicit path names passes through untouched.
PY_SUFFIXES = (".py", ".pyi")


def public_modules(package: Package) -> tuple[str, ...]:
    """The modules that declare *package*'s public API.

    A root that is a regular package, or a single module, is its own
    declaration. A public package beneath a root keeps its own import
    path and is declared in the root's ``__init__`` as well, imported
    for the checkers alone and listed in ``__all__``: the verifier
    follows the declaration into it, reading the ``py.typed`` at the
    distribution's root. A namespace root, one with no ``__init__``,
    declares nothing; a package with no src tree is its distribution
    name's module.
    """
    src = package.directory / "src"
    if not src.is_dir():
        return (module_for(package),)
    modules: list[str] = []
    for root in module_roots(package) or (module_for(package),):
        directory = src.joinpath(*root.split("."))
        if (directory / "__init__.py").is_file() or directory.with_suffix(
            ".py"
        ).is_file():
            modules.append(root)
    return tuple(modules)


def compile_commands(package: Package) -> Path | None:
    """None: a python package's build writes no compilation database."""
    del package
    return None


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

        That is a package's ``__init__.py``.
        """
        src = self._package.directory / "src"
        modules = [*src.rglob("__init__.py")] if src.is_dir() else []
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


def gate_build(package: Package, root: Path) -> None:
    """Nothing: python tests run on source, so there is nothing to build."""
    del package, root


def test(
    package: Package,
    root: Path,
    *,
    selection: tuple[str, ...] = (),
    arguments: tuple[str, ...] = (),
) -> None:
    """Run *package*'s tests: its suite, or the files in *selection* alone.

    *arguments* go to pytest after the workshop's own.
    """
    files = tuple(f"{package.path}/{path}" for path in selection)
    run_test(
        *arguments,
        packages=(package,),
        root=root,
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


def run_examples(
    package: Package,
    root: Path,
    files: tuple[str, ...] = (),
    arguments: tuple[str, ...] = (),
) -> None:
    """Run *package*'s documentation examples, one test per file; *arguments* to pytest.

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
    result = pytest.opts(in_process=False, cwd=root, nofail=True)(*targets, *arguments)
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


#: coverage.py's own configuration file, which the test step's
#: extension writes at the root while the suites are measured: the
#: meter armed in every process the tests start reads it, and the
#: coverage CLI finds it first.
COVERAGE_CONFIG = ".coveragerc"

#: coverage.py's own variable: set in pytest's environment to the
#: configuration's path, it starts the meter in every python the tests
#: start, from interpreter start.
METER = "COVERAGE_PROCESS_START"

#: Who prints a machine's coverage preview after its suites ran: the
#: function a listed extension answers with, given the workspace root
#: and the packages the run tested; None when no extension answers.
PREVIEW: Query[Callable[[Path, tuple[Package, ...]], None] | None] = Query(
    "python.test.preview"
)


def run_test(
    *pytest_args: str,
    packages: tuple[Package, ...],
    root: Path,
    selection: Mapping[str, tuple[str, ...]] | None = None,
    point: str = "",
    measured: bool = False,
) -> None:
    """Run the suites of *packages* in one pytest call, *pytest_args* unchanged.

    The call collects each package's own test directory, the
    workspace's own tests when their unit is among *packages*, and
    nothing else, so a package a run leaves out is never collected;
    *selection* names, per package path, the test files that stand for
    the package's directory. *point* selects that CI point's tests,
    through the workshop's points plugin. A *measured* run measures
    coverage over the namespace the configuration names: inside CI the
    tests run metered for the gate job's union, and on a machine the
    run prints the preview a listed extension answers
    ``python.test.preview`` with
    ([livery.extensions.python._backend.PREVIEW][]), the coverage
    extension's figure beside each floor, or none. A run that is not
    measured runs no meter and prints no preview. A machine's run sets
    the runner's variables (`RUNNER_VARIABLES`), so the suite is judged
    the way the legs judge it. With no test directory among the
    packages no pytest starts, since one handed no path would collect
    the whole configuration.
    """
    from livery.workshop._packages import WORKSPACE_TESTS

    if point:
        pytest_args = (*pytest_args, f"--workshop-point={point}")
    chosen = selection or {}
    picked: list[str] = []
    for package in packages:
        # The workspace's own tests are a unit whose directory is its
        # own: the leg stores them keyed by the whole tree.
        unit = package.path == WORKSPACE_TESTS
        tests = package.directory if unit else package.directory / "tests"
        if tests.is_dir():
            own = package.path if unit else f"{package.path}/tests"
            picked.extend(chosen.get(package.path) or (own,))
    dirs = tuple(picked)
    if not dirs:
        names = ", ".join(package.path for package in packages)
        print(f"  tests: no test directory in {names}; nothing to collect")
        return
    from livery.workshop._ci_context import run_context
    from livery.workshop._pytest_speed import FILE_VARIABLE

    run = run_context()
    with tempfile.TemporaryDirectory() as scratch:
        # The workshop's speed plugin sums each package's test time
        # into this file; the sums print beside the marks afterwards,
        # on a red run too.
        sums = Path(scratch) / "speed.json"
        env = {**os.environ, FILE_VARIABLE: str(sums)}
        try:
            if run is not None:
                # Inside CI a measured run meters the tests, and only
                # them. Coverage's own subprocess variable is armed in
                # pytest's environment, never in the gate's own, so
                # every python the tests start inherits it (the xdist
                # workers, the runner's children a test spawns in a
                # fixture workspace) and the driver that decides what
                # to run records nothing: a line counts only when a
                # test reached it, whatever the gate ran around the
                # tests. The test extension's pytest plugin names each
                # test's context in that run, the leg splits the one
                # run's data per suite, and the floors are judged
                # once, on the union, in the gate job. Captured, and
                # printed on a red exit: the job's log is the run's
                # evidence and nothing else in a runner shows a red
                # test's words. Captured rather than streamed because a
                # captured child gets its own hidden console on Windows,
                # where a streamed one shares the runner's and a control
                # event a worker raises would interrupt the runner too.
                armed = {**env, METER: str(root / COVERAGE_CONFIG)} if measured else env
                result = pytest.opts(in_process=False, env=armed, nofail=True)(
                    *dirs, *pytest_args
                )
                if result.code != 0:
                    print(result.stdout, end="")
                    print(result.stderr, end="")
                    fail(f"pytest exited {result.code}")
            else:
                # Bare --cov on a measured run: the measured source is
                # the configuration's `[run] source`, the namespace the
                # render derived, never a spelled module. The runner's
                # variables are set, so a test that reads them is
                # judged here as on the leg.
                metered = ("--cov", "--cov-report=") if measured else ()
                pytest.opts(in_process=False, env=runner_shaped(env))(
                    *dirs, *metered, *pytest_args
                )
        finally:
            for line in speed_lines(root, sums, leg=run.leg if run else ""):
                print(line)
    if run is None and measured:
        from livery.workshop import answer

        preview = answer(root, PREVIEW)
        if preview is not None:
            preview(root, packages)


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
    """The import prefixes *package* owns.

    The build backend's ``module-name`` where the manifest declares
    one, since that is what the wheel ships. Otherwise each topmost
    directory under ``src`` holding an ``__init__.py`` on its branch,
    which finds nothing under a namespace root, one with no public
    names. Never derived
    from the distribution name: three distributions share the
    ``livery.toolroom`` namespace, so a transformed name would
    attribute two of them to the third. A package with no python
    source owns nothing.
    """
    src = package.directory / "src"
    if not src.is_dir():
        return ()
    declared = _declared_modules(package)
    if declared:
        return declared
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


def _declared_modules(package: Package) -> tuple[str, ...]:
    """The modules uv's build backend ships for *package*, sorted; empty if none."""
    pyproject = package.directory / "pyproject.toml"
    if not pyproject.is_file():
        return ()
    data = tomllib.loads(pyproject.read_text("utf-8"))
    backend = data.get("tool", {}).get("uv", {}).get("build-backend", {})
    declared = backend.get("module-name") if isinstance(backend, dict) else None
    if isinstance(declared, str):
        return (declared,)
    if isinstance(declared, list):
        return tuple(sorted(name for name in declared if isinstance(name, str)))
    return ()


#: The runner's distribution. Its own sources implement the answer to
#: whether a person is there, and it loads the modules a package names
#: under its entry-point groups.
RUNNER_DISTRIBUTION = "livery-footman"

#: The entry-point groups whose modules a program loads as plugins, each
#: to that program's distribution. A module named in one runs inside the
#: program alone, so what the program brings is present whenever the
#: module runs.
PLUGIN_GROUPS: Mapping[str, str] = {
    "footman.tasks": RUNNER_DISTRIBUTION,
    "footman.builtin": RUNNER_DISTRIBUTION,
    "pytest11": "pytest",
}


def plugin_hosts(package: Package) -> dict[str, frozenset[str]]:
    """Each module *package* names in a plugin group, to the programs that load it.

    Read from the ``[project.entry-points]`` tables of the package's
    ``pyproject.toml``, for the groups ``PLUGIN_GROUPS`` names, each
    program by its distribution. A module under one of these modules
    is loaded with it. Empty for a package that names none.
    """
    pyproject = package.directory / "pyproject.toml"
    if not pyproject.is_file():
        return {}
    data = tomllib.loads(pyproject.read_text("utf-8"))
    groups = data.get("project", {}).get("entry-points", {})
    hosts: dict[str, set[str]] = {}
    for group, program in PLUGIN_GROUPS.items():
        for target in (groups.get(group) or {}).values():
            module = str(target).partition(":")[0].strip()
            hosts.setdefault(module, set()).add(program)
    return {module: frozenset(programs) for module, programs in hosts.items()}


def plugin_modules(package: Package) -> tuple[str, ...]:
    """The modules *package* declares as footman task entry points.

    A module the runner loads as a plugin has the runner present by
    construction, and whatever the extension stack mounts with it, so its
    imports of either are not dependencies of the distribution. The
    fact lives in the package's own metadata; nothing here needs to
    be told a second time.
    """
    return tuple(
        module
        for module, programs in plugin_hosts(package).items()
        if RUNNER_DISTRIBUTION in programs
    )


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


def references(package: Package) -> tuple[SourceReferences, ...]:
    """What each python source of *package* references, one record per file.

    Every ``.py`` file under ``src``, then under ``tests``, in path
    order, read through the layering phase's one parse, so a file
    another rule also reads is parsed once per gate; a file that does
    not parse is left out, since the syntax gate names it. A source
    under ``src`` is referenced by its dotted path, and a test by
    nothing. Two of this kind's conventions account for a name: each
    name a module the package declares as a footman task entry point
    references, since the runner that loads the module brings what it
    imports, and each name of a sibling one of its optional extras
    names, since an extra is a declaration of its own.
    """
    from livery.extensions.python._parse import dotted_name, parsed_source

    plugins = plugin_modules(package)
    around, named = _extra_siblings(package)
    records: list[SourceReferences] = []
    for area in ("src", "tests"):
        base = package.directory / area
        if not base.is_dir():
            continue
        for source in sorted(base.rglob("*.py")):
            parsed = parsed_source(source)
            if parsed is None:
                continue
            module = dotted_name(source, base) if area == "src" else ""
            names = dict.fromkeys(reference.name for reference in parsed.references)
            loaded = bool(module) and any(
                module == plugin or module.startswith(plugin + ".")
                for plugin in plugins
            )
            accounted = tuple(
                name for name in names if loaded or around.owner_of(name) in named
            )
            relative = source.relative_to(package.directory).as_posix()
            records.append(
                SourceReferences(
                    path=f"{package.path}/{relative}",
                    module=module,
                    references=parsed.references,
                    accounted=accounted,
                )
            )
    return tuple(records)


def _extra_siblings(package: Package) -> tuple[Neighbours, frozenset[str]]:
    """Who owns each name in the workspace, and the siblings *package*'s extras name.

    The siblings are the other members whose distribution an optional
    extra of the package names, by path; a name is theirs when the
    longest member prefix it matches is one of their roots. Empty for a
    package that declares no extra, which then reads no other member.
    """
    from livery.workshop._packages import discover_packages, neighbours

    extras = _extra_distributions(package)
    if not extras:
        return Neighbours(owners={}, by_path={}), frozenset()
    root = package.directory.parents[len(PurePosixPath(package.path).parts) - 1]
    around = neighbours(discover_packages(root))
    named = frozenset(
        path
        for path, member in around.by_path.items()
        if member.name in extras and path != package.path
    )
    return around, named


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


def module_docs_dir(package: Package) -> Path | None:
    """Where *package*'s wheel-embedded ``_docs`` lives; None without a module.

    In the shallowest of the package's module roots (`module_roots`)
    whose directory exists under ``src``, the importable package the
    wheel ships. Of two roots with as many parts, the one answered
    first.
    """
    src = package.directory / "src"
    directories = [src.joinpath(*root.split(".")) for root in module_roots(package)]
    present = [directory for directory in directories if directory.is_dir()]
    if not present:
        return None
    return min(present, key=lambda directory: len(directory.parts)) / "_docs"


def materialise_module_docs(package: Package) -> Path | None:
    """Copy the package's ``docs/`` into its wheel-embedded ``_docs``, whole.

    The copy is rebuilt from scratch, so the wheel never carries docs
    older than the tree it is built from, and a package without
    ``docs/`` loses its old copy instead of shipping it.

    Returns:
        The copy's path; None when the package has no module to carry
        it, or no ``docs/`` to copy.
    """
    target = module_docs_dir(package)
    if target is None:
        return None
    shutil.rmtree(target, ignore_errors=True)
    docs = package.directory / "docs"
    if not docs.is_dir():
        return None
    shutil.copytree(docs, target)
    return target


@contextmanager
def module_docs(package: Package) -> Generator[Path | None]:
    """The wheel-embedded ``_docs``, in the source tree for one build alone.

    Copied whole on entry, as `materialise_module_docs` copies it, and
    removed on exit, after a failed build too. A copy left behind puts
    the docs' example files under the package's source, where every
    checker that walks the tree judges them as the package's own code.
    """
    target = materialise_module_docs(package)
    try:
        yield target
    finally:
        if target is not None:
            shutil.rmtree(target, ignore_errors=True)


def build(package: Package, root: Path, *, epoch: int = 0) -> Path:
    """Build *package*'s wheel and sdist into its ``dist/``; the dist dir.

    Always from a clean ``dist/``: reusing an artifact from an
    earlier build installs stale code under current tests, a silent
    footgun bought off for seconds of build. *epoch* (a commit's
    timestamp) rides ``SOURCE_DATE_EPOCH`` so a pure-Python rebuild
    at the same ref is byte-identical.
    """
    dist = package.directory / "dist"
    shutil.rmtree(dist, ignore_errors=True)
    env = dict(os.environ)
    if epoch:
        env["SOURCE_DATE_EPOCH"] = str(epoch)
    # The wheel embeds the docs copied whole from packages/<name>/docs
    # for this build, so it never carries docs older than its tree.
    with module_docs(package):
        result = tools.uv.opts(
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


def _exported(root: Path, pins: Path, *selection: str) -> Path | None:
    """The workspace lock's *selection* as exact pins in *pins*; None without a lock.

    ``uv export`` reads the lock offline. Workspace members are left
    out: they export as workspace-relative paths a scratch venv cannot
    resolve, and the leg installs the built wheel and finds a sibling's
    in the co-released set's ``dist/`` directories alone. A workspace
    without a lock (a bare rig, a consumer checkout) answers None.
    """
    result = tools.uv.opts(cwd=root, nofail=True, recorded=False)(
        "export",
        "--format",
        "requirements-txt",
        *selection,
        "--no-emit-workspace",
        "--no-hashes",
        "-o",
        str(pins),
    )
    if result.code != 0 or not pins.is_file():
        return None
    # A path-sourced entry (a checkout standing in for a wheel)
    # exports as a local reference the scratch venv can neither
    # reach nor parse; the pins we want are the index-resolvable
    # lines, so the local ones are dropped.
    lines = [
        line
        for line in pins.read_text("utf-8").splitlines()
        if "file://" not in line and not line.startswith(("-e ", "./", "/"))
    ]
    pins.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return pins


def _dev_pins(root: Path, scratch: Path) -> Path | None:
    """The lock's dev-group resolution as exact pins; None without one.

    The isolated venv's toolchain arrives at the versions the gate
    itself tested with. Workspace members stay out: the leg already
    installed the built wheel at its resolution, and reinstalling them
    would clobber the starved resolution the movement guard protects.
    A workspace without a lock or a dev group answers None and the leg
    falls back to a bare pytest install.
    """
    return _exported(
        root, scratch / "dev-pins.txt", "--only-group", "dev", "--no-emit-project"
    )


def _lock_pins(root: Path, scratch: Path) -> Path | None:
    """Every version the workspace lock pins, as it stands; None without a lock.

    The head leg's constraints: every package, group and extra the
    lock resolves, read with ``--frozen``, so the export never locks
    again and the leg installs at the pins the gate tested.
    """
    return _exported(
        root,
        scratch / "lock-pins.txt",
        "--frozen",
        "--all-packages",
        "--all-groups",
        "--all-extras",
    )


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
    the ambient coverage variables go, as every read of measured data
    scrubs them: a metered gate must not re-point the leg's children.
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


#: The resolutions the isolated test takes, each to uv's resolution
#: strategy and whether the workspace lock's pins constrain the install.
RESOLUTIONS: dict[str, tuple[str, bool]] = {
    "floor": ("lowest-direct", False),
    "latest": ("highest", False),
    "head": ("highest", True),
}


def isolated_test(
    package: Package, root: Path, *, release_dirs: tuple[Path, ...], resolution: str
) -> dict[str, str]:
    """Test *package*'s built wheel in a fresh venv at *resolution*; what it got.

    [livery.extensions.python._backend.run_isolated_test][] runs it.
    """
    return run_isolated_test(
        package, root, release_dirs=release_dirs, resolution=resolution
    )


def verify(
    package: Package, *, tree: Path, version: str, python: str, index: str, extras: str
) -> int:
    """Install *package*'s released *version* alone and run its tests at *tree*.

    The exit code; [livery.extensions.python._verify.verify][] runs it.
    """
    from livery.extensions.python import _verify

    return _verify.verify(
        package, tree=tree, version=version, python=python, index=index, extras=extras
    )


def run_isolated_test(
    package: Package,
    root: Path,
    *,
    release_dirs: tuple[Path, ...] = (),
    resolution: str = "latest",
    wheels_dir: Path | None = None,
) -> dict[str, str]:
    """Install the built wheel into a fresh venv and test the installed copy.

    One leg of the release's isolated test, at *resolution*
    ([livery.extensions.python._backend.RESOLUTIONS][]): ``floor``
    puts every direct dependency at its declared floor, so a floor
    lying about compatibility fails here; ``latest`` resolves the
    newest, the world a fresh consumer gets; ``head`` installs at the
    versions the workspace lock pins, read as it stands, and refuses
    in a workspace without a lock.

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

    Raises:
        Failed: when *resolution* is none of the three, when the head
            leg finds no lock, when an install fails, when the
            toolchain install moves a direct dependency the leg
            resolved, and when the tests fail.
    """
    import json
    import tempfile

    if resolution not in RESOLUTIONS:
        fail(
            f"{package.name}: the isolated test resolves at"
            f" {', '.join(RESOLUTIONS)}, not {resolution!r}"
        )
    strategy, locked = RESOLUTIONS[resolution]

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

        constraints: tuple[str, ...] = ()
        if locked:
            pins = _lock_pins(root, Path(scratch))
            if pins is None:
                fail(
                    f"{package.name}: the head leg installs at the versions the"
                    f" workspace lock pins, and uv export read no lock in {root};"
                    " lock the workspace, or list floor or latest in"
                    " [release] prove"
                )
            constraints = ("--constraint", str(pins))
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
            f"--resolution={strategy}",
            *[f"--find-links={d}" for d in release_dirs],
            *_refresh_args(release_dirs),
            *_index_args(root),
            *constraints,
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
                    # finds the workspace's configuration, whose addopts
                    # would hand the leg xdist workers and coverage flags;
                    # the leg is serial and unmetered by design.
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
