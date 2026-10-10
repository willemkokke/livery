"""The gate build of a CMake project: configure, compile, and run its tests.

The build lands in the package's ``build/gate`` directory, configured
with the Ninja generator and a compilation database for the tools that
read one. The tests run through ctest, measured by the compiler family
CMake detected: gcov for gcc, llvm-cov for clang, Microsoft's engine
for MSVC. On Windows with no compiler chosen, the build enters the
newest Visual Studio's C++ environment itself. Nothing here names a
language: CMake chooses the compilers its project asks for.

Every tool here is a tool of the store, reached through its toolroom
handle, so the version is the one this checkout's lock pins. A handle
spawns its tool by name, so a machine that never deployed one raises
``OSError``, which becomes a refusal naming the sync that supplies it.
"""

from __future__ import annotations

import functools
import json
import os
import platform
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn
from xml.etree import ElementTree

import livery.footman as footman
import livery.toolroom.tools as tools
from livery.footman import fail

if TYPE_CHECKING:
    from livery.toolroom.tools import Result
    from livery.workshop._packages import Package


#: Where the gate's cmake configure lands, under the package.
#: conan's own cmake_layout also builds under build/, so one
#: gitignore entry covers both.
GATE_BUILD_DIR = "build/gate"


#: The include the gate's configure hands CMake, shipped beside this
#: module: the instrumentation each compiler family takes, so the
#: measurer beside the compiler can read the run.
COVERAGE_CMAKE = Path(__file__).with_name("coverage.cmake")


#: Where an instrumented test run leaves its profiles and reports,
#: under the gate build.
PROFILE_DIR = "coverage"


#: The shell that reads the batch file MSVC's environment comes from:
#: a Windows component, never a tool of the store.
SHELL = "cmd.exe"


#: Where the Visual Studio installer keeps vswhere, under the 32-bit
#: program files directory: the one path Microsoft documents as stable
#: across versions and editions.
VSWHERE = ("Microsoft Visual Studio", "Installer", "vswhere.exe")


#: Per host architecture, the Visual Studio component that is the C++
#: build tools for it and the batch file that enters their
#: environment; the x64 pair serves every architecture not named.
MSVC_TOOLS = {
    "ARM64": ("Microsoft.VisualStudio.Component.VC.Tools.ARM64", "vcvarsarm64.bat"),
}


MSVC_TOOLS_X64 = ("Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "vcvars64.bat")


def toolchain_env() -> dict[str, str]:
    """The environment the gate's build tools run in.

    This process's environment, entered into MSVC's on Windows: with
    no ``CXX`` set and no ``cl`` on PATH, vswhere names the newest
    Visual Studio that has the C++ build tools for this architecture,
    and the environment its vcvars batch file leaves is read back
    through ``set``. ``CC`` and ``CXX`` then name ``cl``, so CMake
    takes MSVC over a MinGW gcc that is also on PATH: on Windows the
    gate builds with MSVC unless ``CXX`` says otherwise, and a person
    who sets it chooses. Read once per process; a copy each call.

    Raises:
        Failed: on Windows with no compiler chosen and no Visual
            Studio installation with the C++ build tools to enter.
    """
    return dict(_entered())


@functools.cache
def _entered() -> dict[str, str]:
    """The environment `toolchain_env` copies, read once per process."""
    env = dict(os.environ)
    if not _on_windows() or env.get("CXX"):
        return env
    if not shutil.which("cl", path=env.get("PATH", "")):
        env = _msvc_environment(env)
    return {**env, "CC": env.get("CC") or "cl", "CXX": "cl"}


def _on_windows() -> bool:
    """Whether this is Windows, where the gate enters MSVC's environment itself."""
    return sys.platform == "win32"


def vswhere_path(env: dict[str, str] | None = None) -> Path:
    """Where this machine keeps vswhere, present or not.

    Under the 32-bit program files directory *env* names, this
    process's when *env* is None; the key is read in both spellings,
    since Windows upper-cases the ones a process inherits.
    """
    variables = dict(os.environ) if env is None else env
    program_files = (
        variables.get("PROGRAMFILES(X86)")
        or variables.get("ProgramFiles(x86)")
        or r"C:\Program Files (x86)"
    )
    return Path(program_files).joinpath(*VSWHERE)


def _msvc_environment(env: dict[str, str]) -> dict[str, str]:
    """*env* entered into the newest Visual Studio's C++ build tools.

    Raises:
        Failed: when vswhere is not installed, names no installation
            with the C++ build tools for this architecture, or the
            tools' batch file leaves no toolset in the environment.
    """
    vswhere = vswhere_path(env)
    if not vswhere.is_file():
        fail(
            "no C++ compiler is on PATH and Visual Studio's installer is not at"
            f" {vswhere}; install the Build Tools with the C++ workload, or set"
            " CXX to the compiler to build with"
        )
    component, batch = MSVC_TOOLS.get(platform.machine().upper(), MSVC_TOOLS_X64)
    installation = _asked(
        [
            str(vswhere),
            "-latest",
            "-products",
            "*",
            "-requires",
            component,
            "-property",
            "installationPath",
        ]
    )
    if not installation:
        fail(
            "no C++ compiler is on PATH and no Visual Studio installation has the"
            f" C++ build tools ({component}); install the workload, or set CXX to"
            " the compiler to build with"
        )
    script = Path(installation) / "VC" / "Auxiliary" / "Build" / batch
    entered = _from_set_output(_shell_set(f'@call "{script}" >nul\n@set\n'))
    if "VCTOOLSINSTALLDIR" not in entered:
        fail(
            f"{script} left no VCToolsInstallDir in the environment, so the C++"
            f" build tools of {installation} cannot be entered; repair the"
            " installation, or set CXX to the compiler to build with"
        )
    return entered


def _shell_set(script: str) -> str:
    """What ``set`` prints after *script* ran in cmd.exe; empty when it will not run."""
    with tempfile.TemporaryDirectory(prefix="workshop-msvc-") as home:
        batch = Path(home) / "enter.cmd"
        batch.write_text(script, encoding="utf-8")
        try:
            answer = footman.run(
                [SHELL, "/d", "/c", str(batch)],
                nofail=True,
                recorded=False,
                timeout=120,
            )
        except (OSError, footman.TimedOut):
            return ""
    return (answer.stdout or "") if answer.code == 0 else ""


def _from_set_output(text: str) -> dict[str, str]:
    """The environment ``set`` printed, keys upper-cased as Windows compares them.

    cmd's hidden variables print with an empty key and are dropped,
    as is any line without ``=``.
    """
    parsed: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and key:
            parsed[key.upper()] = value
    return parsed


def configure(package: Package, arguments: tuple[str, ...] = ()) -> None:
    """Configure *package* into the gate's build directory; *arguments* go to cmake.

    CMake configures against the host's toolchain with the Ninja
    generator, in the environment ``toolchain_env`` enters, and
    exports the compile commands clang-tidy reads. A dependency the
    project's ``CMakeLists.txt`` cannot find fails the configure with
    cmake's own message.
    """
    build_dir = package.directory / GATE_BUILD_DIR
    tools.cmake.opts(cwd=package.directory, env=toolchain_env())(
        "-S",
        ".",
        "-B",
        str(build_dir),
        "-G",
        "Ninja",
        "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
        f"-DCMAKE_PROJECT_INCLUDE={COVERAGE_CMAKE}",
        *arguments,
    )


def compile(package: Package, arguments: tuple[str, ...] = ()) -> None:
    """Build the configured *package*, *arguments* to cmake's build; incremental."""
    build_dir = package.directory / GATE_BUILD_DIR
    tools.cmake.opts(cwd=package.directory, env=toolchain_env())(
        "--build", str(build_dir), *arguments
    )


def gate_build(package: Package, root: Path) -> None:
    """Configure and build *package* into the gate's build directory.

    What the kind's tests run on, in one call, for the affected
    gate's test-only step; the two halves are the ``configure`` and
    ``build`` checks the kind registers.
    """
    del root
    configure(package)
    compile(package)


def test(
    package: Package,
    root: Path,
    *,
    selection: tuple[str, ...] = (),
    arguments: tuple[str, ...] = (),
) -> None:
    """Run ctest over the gate build, measured: every test, or *selection*'s alone.

    *arguments* go to ctest after the workshop's own.

    A selected test file maps to the ctest named after its stem
    (``tests/test_acme.cpp`` runs ``test_acme``), which is how the
    template registers tests; a selection no ctest answers to is a
    refusal naming the rule. The run is measured by the family of
    the compiler CMake configured the gate build with: gcov and
    llvm-cov read the counters a green run left, and Microsoft's
    engine collects around the run itself. The lines reached land as
    the package's part at the workspace *root*; a red run leaves
    none, and a family without a measurer refuses by name, since a
    suite that ran unmeasured never passes as measured.
    """
    from livery.workshop import _coverage_lines as lines_

    build_dir = package.directory / GATE_BUILD_DIR
    env = {
        **toolchain_env(),
        "CTEST_OUTPUT_ON_FAILURE": "1",
        **_fresh_profiles(package),
    }
    names = [Path(path).stem for path in selection]
    argv = ["--test-dir", str(build_dir), "--output-on-failure"]
    if names:
        pattern = "^(" + "|".join(re.escape(name) for name in names) + ")$"
        argv += ["-R", pattern]
    argv += arguments
    compiler_id, compiler = compiler_of(package)
    family = lines_.measurer_for(compiler_id)
    if family == "msvc":
        measured = _measure_msvc(package, argv, env, names)
    else:
        _run_ctest(package, argv, env, names)
        if not family:
            fail(
                f"{package.name}: the gate build's compiler"
                f" {compiler_id or 'is unknown'} has no coverage measurer; the"
                f" families are {', '.join(sorted(lines_.FAMILIES))}"
            )
        measured = (
            _measure_gcov(package, compiler)
            if family == "gcov"
            else _measure_llvm(package, compiler)
        )
    kept = lines_.within(lines_.relativise(measured, root), package)
    lines_.write_part(root, package.path, kept)
    print(f"  {package.name}: coverage: {len(kept)} file(s) measured by {family}")


def _run_ctest(
    package: Package, arguments: list[str], env: dict[str, str], names: list[str]
) -> None:
    """Run ctest with *arguments*; a red run or an unanswered selection refuses.

    ctest is an entry point of the cmake record, so the store deploys
    the two together and one handle each reaches them.
    """
    try:
        ran = tools.ctest.opts(
            cwd=package.directory, env=env, nofail=True, recorded=False
        )(*arguments)
    except OSError:
        from livery.workshop._tools import undeployed

        undeployed("ctest")
    if names and "No tests were found" in ran.stdout + ran.stderr:
        _no_ctest_named(package, names)
    if ran.code != 0:
        fail(
            f"{package.name}: ctest failed (exit {ran.code}):\n"
            f"{ran.stdout[-4000:]}{ran.stderr[-2000:]}"
        )


def _no_ctest_named(package: Package, names: list[str]) -> NoReturn:
    """Refuse a selection no ctest answers to, naming the rule that maps them."""
    fail(
        f"{package.name}: no ctest is named {', '.join(names)}; the cpp-conan"
        " kind maps a test file to the ctest of its stem"
        " (add_test(NAME <stem> ...)), so register it or run the suite"
    )


def _fresh_profiles(package: Package) -> dict[str, str]:
    """Clear the last run's counters and name where the next run's profiles land.

    gcov adds a run's counts to the ``.gcda`` files the build left,
    and llvm writes one ``.profraw`` per process wherever
    ``LLVM_PROFILE_FILE`` points, so both are cleared first and the
    run measures itself alone.
    """
    build_dir = package.directory / GATE_BUILD_DIR
    for stale in build_dir.rglob("*.gcda"):
        stale.unlink()
    profiles = build_dir / PROFILE_DIR
    profiles.mkdir(parents=True, exist_ok=True)
    for stale in profiles.glob("*.profraw"):
        stale.unlink()
    return {"LLVM_PROFILE_FILE": str(profiles / "%p-%m.profraw")}


def compiler_of(package: Package) -> tuple[str, str]:
    """The gate build's C++ compiler id and path, as CMake detected them.

    Read from the compiler file CMake writes on configure; both empty
    for a package whose gate build is not configured.
    """
    build_dir = package.directory / GATE_BUILD_DIR
    found = sorted(build_dir.glob("CMakeFiles/*/CMakeCXXCompiler.cmake"))
    if not found:
        return "", ""
    text = found[-1].read_text(encoding="utf-8", errors="replace")
    identity = re.search(r'set\(CMAKE_CXX_COMPILER_ID "([^"]*)"\)', text)
    compiler = re.search(r'set\(CMAKE_CXX_COMPILER "([^"]*)"\)', text)
    return (
        identity.group(1) if identity else "",
        compiler.group(1) if compiler else "",
    )


def _beside(compiler: str, *names: str) -> str:
    """The first of *names* beside *compiler*, then on PATH; empty when none is."""
    home = Path(compiler).parent if compiler else None
    for name in names:
        if home is not None:
            for candidate in (home / name, (home / name).with_suffix(".exe")):
                if candidate.is_file():
                    return str(candidate)
        found = shutil.which(name)
        if found:
            return found
    return ""


def _xcrun(name: str) -> str:
    """Where the SDK keeps *name*, on macOS; empty elsewhere or when it has none."""
    if sys.platform != "darwin":
        return ""
    return _asked(["xcrun", "--find", name])


def _measure_gcov(package: Package, compiler: str) -> dict[str, dict[int, int]]:
    """The lines gcov reads from the build's counters.

    A file is named as the compiler saw it; one relative to the build
    directory is made absolute there.
    """
    from livery.workshop import _coverage_lines as lines_

    build_dir = package.directory / GATE_BUILD_DIR
    data = sorted(build_dir.rglob("*.gcda"))
    if not data:
        fail(
            f"{package.name}: the test run left no .gcda counter under"
            f" {GATE_BUILD_DIR}; the build was configured without"
            f" {COVERAGE_CMAKE.name}, so configure it again"
        )
    suffix = re.search(r"-(\d+)$", Path(compiler).name)
    names = [f"gcov-{suffix.group(1)}"] if suffix else []
    gcov = _beside(compiler, *names, "gcov")
    if not gcov:
        fail(
            f"{package.name}: built with {compiler}, and no gcov is beside it or"
            " on PATH; install the compiler's gcov"
        )
    merged: dict[str, dict[int, int]] = {}
    for path in data:
        result = footman.run(
            [gcov, "--json-format", "--stdout", str(path)],
            cwd=build_dir,
            nofail=True,
            recorded=False,
        )
        if result.code != 0:
            fail(
                f"{package.name}: gcov exited {result.code} on {path.name}:\n"
                f"{result.stdout[-2000:]}{result.stderr[-2000:]}"
            )
        read = lines_.from_gcov_json(result.stdout)
        # gcov names a file as the compiler saw it, relative to the
        # build directory when the compile line was.
        absolute = {
            name if Path(name).is_absolute() else str(build_dir / name): counts
            for name, counts in read.items()
        }
        merged = lines_.merge(merged, absolute)
    return merged


def _measure_llvm(package: Package, compiler: str) -> dict[str, dict[int, int]]:
    """The lines llvm-cov reads from the run's profiles over the test executables."""
    from livery.workshop import _coverage_lines as lines_

    build_dir = package.directory / GATE_BUILD_DIR
    raws = sorted((build_dir / PROFILE_DIR).glob("*.profraw"))
    if not raws:
        fail(
            f"{package.name}: the test run left no .profraw under"
            f" {GATE_BUILD_DIR}/{PROFILE_DIR}; the build was configured without"
            f" {COVERAGE_CMAKE.name}, so configure it again"
        )
    profdata = _beside(compiler, "llvm-profdata") or _xcrun("llvm-profdata")
    cov = _beside(compiler, "llvm-cov") or _xcrun("llvm-cov")
    if not profdata or not cov:
        fail(
            f"{package.name}: built with {compiler}, and llvm-profdata or"
            " llvm-cov is not beside it, on PATH, or where xcrun looks; install"
            " the compiler's llvm tools"
        )
    merged = build_dir / PROFILE_DIR / "merged.profdata"
    result = footman.run(
        [profdata, "merge", "-sparse", *(str(raw) for raw in raws), "-o", str(merged)],
        nofail=True,
        recorded=False,
    )
    if result.code != 0:
        fail(
            f"{package.name}: llvm-profdata exited {result.code}:\n"
            f"{result.stdout[-2000:]}{result.stderr[-2000:]}"
        )
    objects = _test_objects(package)
    if not objects:
        fail(f"{package.name}: ctest names no test executable to read coverage from")
    argv = [cov, "export", "-format=lcov", f"-instr-profile={merged}", objects[0]]
    for extra in objects[1:]:
        argv += ["-object", extra]
    result = footman.run(argv, nofail=True, recorded=False)
    if result.code != 0:
        fail(
            f"{package.name}: llvm-cov exited {result.code}:\n"
            f"{result.stdout[-2000:]}{result.stderr[-2000:]}"
        )
    return lines_.from_lcov(result.stdout)


def _measure_msvc(
    package: Package, arguments: list[str], env: dict[str, str], names: list[str]
) -> dict[str, dict[int, int]]:
    """The lines Microsoft's engine reads from the ctest run it collects around.

    Each executable ctest names, and every shared library the build
    made, is instrumented statically into a copy that takes the
    original's place for the run and gives it back after; what the
    engine wrote beside them, the copy's symbols and the engine's own
    runtime, goes with it, so the build's own output is never
    instrumented twice. ctest runs under
    ``dotnet-coverage collect`` with child processes included, and
    its verdict is read from the JUnit report it writes, never from
    the collector's exit code, which is the collector's own.

    Raises:
        Failed: when ctest names no executable, an instrumentation
            or the collection fails, a test fails, or no report
            comes out; each names what happened.
    """
    from livery.workshop import _coverage_lines as lines_

    profiles = package.directory / GATE_BUILD_DIR / PROFILE_DIR
    objects = _test_objects(package)
    if not objects:
        fail(f"{package.name}: ctest names no test executable to instrument")
    ctest = _ctest_program()
    settings = profiles / "coverage.config"
    settings.write_text(_msvc_settings(objects), encoding="utf-8")
    report = profiles / "coverage.cobertura.xml"
    verdict = profiles / "ctest.xml"
    for stale in (report, verdict):
        stale.unlink(missing_ok=True)
    homes = {Path(name).parent for name in objects}
    before = {path for home in homes for path in home.iterdir()}
    run_env = {
        **env,
        "DOTNET_COVERAGE_TELEMETRY_OPTOUT": "1",
        "DOTNET_COVERAGE_NOLOGO": "1",
    }
    swapped: list[tuple[Path, Path]] = []
    try:
        for name in objects:
            original = Path(name)
            instrumented = original.with_name(
                f"{original.stem}.instrumented{original.suffix}"
            )
            result = _dotnet_coverage(
                package,
                run_env,
                "instrument",
                "--nologo",
                "-s",
                str(settings),
                "-o",
                str(instrumented),
                str(original),
            )
            if result.code != 0 or not instrumented.is_file():
                fail(
                    f"{package.name}: dotnet-coverage could not instrument"
                    f" {original.name} (exit {result.code}):\n"
                    f"{result.stdout[-2000:]}{result.stderr[-2000:]}"
                )
            kept = original.with_name(f"{original.name}.uninstrumented")
            os.replace(original, kept)
            os.replace(instrumented, original)
            swapped.append((original, kept))
        result = _dotnet_coverage(
            package,
            run_env,
            "collect",
            "--nologo",
            "-s",
            str(settings),
            "-f",
            "cobertura",
            "-o",
            str(report),
            "--",
            ctest,
            *arguments,
            "--output-junit",
            str(verdict),
        )
    finally:
        for original, kept in swapped:
            os.replace(kept, original)
        # The engine writes the instrumented copy's symbols and its own
        # runtime beside the binaries, read during the run; nothing it
        # wrote outlives the run, so the build's output stays its own
        # and the next run instruments a build, never an engine file.
        for home in homes:
            for path in home.iterdir():
                if path not in before and path.is_file():
                    path.unlink()
    output = result.stdout + result.stderr
    _ctest_verdict(package, names, verdict, output)
    if result.code != 0:
        fail(
            f"{package.name}: dotnet-coverage exited {result.code} collecting the"
            f" run:\n{output[-4000:]}"
        )
    if not report.is_file():
        fail(
            f"{package.name}: dotnet-coverage wrote no report at {report}:\n"
            f"{output[-4000:]}"
        )
    return lines_.from_cobertura(report.read_text(encoding="utf-8"))


def _ctest_program() -> str:
    """Where ctest is, for a collector that spawns it by path.

    Raises:
        Failed: when ctest is not deployed, naming the sync that
            supplies it.
    """
    found = shutil.which("ctest")
    if not found:
        from livery.workshop._tools import undeployed

        undeployed("ctest")
    return found


def _dotnet_coverage(package: Package, env: dict[str, str], *args: str) -> Result:
    """One dotnet-coverage invocation through the store's handle.

    Raises:
        Failed: when the tool is not deployed: a workspace measured
            with MSVC requires it, and the refusal names the line.
    """
    try:
        return tools.dotnet_coverage.opts(
            cwd=package.directory, env=env, nofail=True, recorded=False
        )(*args)
    except OSError:
        fail(
            "dotnet-coverage is not on PATH: a cpp-conan package built with MSVC"
            ' is measured by it, so add "dotnet_coverage" to [toolroom] requires'
            f" in workshop.toml, run `{footman.prog()} toolroom.lock`, then"
            f" `{footman.prog()} sync` and the printed env.emit line"
        )


def _msvc_settings(objects: list[str]) -> str:
    """The engine's settings: native instrumentation on, the build's own modules alone.

    A module path is matched by its file name in any letter case,
    since the loader reports a path in the case it has, and the
    pattern stays inside the regular expression syntax every engine
    version reads: no inline flags.
    """
    names = "|".join(_any_case(Path(name).name) for name in objects)
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        "<Configuration>\n"
        "  <CodeCoverage>\n"
        "    <EnableStaticNativeInstrumentation>True"
        "</EnableStaticNativeInstrumentation>\n"
        "    <EnableDynamicNativeInstrumentation>True"
        "</EnableDynamicNativeInstrumentation>\n"
        "    <EnableStaticManagedInstrumentation>False"
        "</EnableStaticManagedInstrumentation>\n"
        "    <EnableDynamicManagedInstrumentation>False"
        "</EnableDynamicManagedInstrumentation>\n"
        "    <UseVerifiableInstrumentation>False</UseVerifiableInstrumentation>\n"
        "    <AllowLowIntegrityProcesses>True</AllowLowIntegrityProcesses>\n"
        "    <CollectFromChildProcesses>True</CollectFromChildProcesses>\n"
        "    <ModulePaths>\n"
        "      <Include>\n"
        f"        <ModulePath>.*[\\\\/]({names})$</ModulePath>\n"
        "      </Include>\n"
        "    </ModulePaths>\n"
        "  </CodeCoverage>\n"
        "</Configuration>\n"
    )


def _any_case(text: str) -> str:
    """A regular expression matching *text* in any letter case, every flavour."""
    return "".join(
        f"[{char.lower()}{char.upper()}]" if char.isalpha() else re.escape(char)
        for char in text
    )


def _ctest_verdict(
    package: Package, names: list[str], report: Path, output: str
) -> None:
    """Refuse unless the JUnit report ctest wrote says every test ran and passed.

    A missing report means ctest never ran to its end, a count of
    zero means no test ran (a selection no ctest answers to, or a
    suite with none registered), and a failure fails the run with
    ctest's *output*.
    """
    if not report.is_file():
        fail(
            f"{package.name}: ctest left no report at {report.name}; its"
            f" output:\n{output[-4000:]}"
        )
    try:
        suite = ElementTree.parse(report).getroot()
    except ElementTree.ParseError as error:
        fail(f"{package.name}: ctest's report is not XML ({error})")
    tests = int(suite.get("tests", "0"))
    failures = int(suite.get("failures", "0"))
    if tests == 0:
        if names:
            _no_ctest_named(package, names)
        fail(f"{package.name}: ctest ran no test; register one with add_test")
    if failures:
        fail(
            f"{package.name}: ctest failed ({failures} of {tests} test(s)):\n"
            f"{output[-4000:]}"
        )


#: The file name of the runtime Microsoft's engine writes beside a
#: binary it instrumented, loaded by the instrumented code: never a
#: library the build made, so never an object to instrument.
ENGINE_RUNTIME = re.compile(r"(static_)?covrun\d*\.dll", re.IGNORECASE)


def _test_objects(package: Package) -> list[str]:
    """The executables ctest runs, then every shared library the build made.

    A library left by a coverage engine's earlier run is not the
    build's and is skipped by name.
    """
    build_dir = package.directory / GATE_BUILD_DIR
    try:
        listed = tools.ctest.opts(cwd=package.directory, nofail=True, recorded=False)(
            "--show-only=json-v1", "--test-dir", str(build_dir)
        )
    except OSError:
        from livery.workshop._tools import undeployed

        undeployed("ctest")
    objects: list[str] = []
    if listed.code == 0:
        try:
            tests = json.loads(listed.stdout).get("tests", [])
        except ValueError:
            tests = []
        for entry in tests:
            command = entry.get("command") if isinstance(entry, dict) else None
            if not isinstance(command, list) or not command:
                continue
            if Path(command[0]).is_file() and command[0] not in objects:
                objects.append(command[0])
    for suffix in (".so", ".dylib", ".dll"):
        objects += [
            str(path)
            for path in sorted(build_dir.rglob(f"*{suffix}"))
            if str(path) not in objects and not ENGINE_RUNTIME.fullmatch(path.name)
        ]
    return objects


def _asked(argv: list[str]) -> str:
    """The first line *argv* prints, or empty when it will not run."""
    try:
        answer = footman.run(argv, nofail=True, recorded=False)
    except OSError:
        return ""
    lines = (answer.stdout or "").strip().splitlines()
    return lines[0] if answer.code == 0 and lines else ""


def compile_commands(package: Package) -> Path | None:
    """Where the package's gate build writes its compilation database."""
    return package.directory / GATE_BUILD_DIR / "compile_commands.json"
