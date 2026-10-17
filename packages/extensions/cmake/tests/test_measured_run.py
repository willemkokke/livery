"""A measured ctest run read by each native measurer, every tool faked: refusals first.

ctest, gcov, llvm-profdata, llvm-cov, the demangler and Microsoft's
engine each answer as a fake tells them, so every refusal and fallback
of a measured run runs on any host, whatever compilers it has. The
programs that enter MSVC's environment on Windows, vswhere and cmd.exe,
are faked the same way.
"""

from __future__ import annotations

import json
import os
import platform
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

import livery.footman as footman
import livery.toolroom.tools as tools
from livery.extensions.cmake import _build as _cmake
from livery.extensions.cmake._readings import write_part
from livery.extensions.coverage._parts import part_files, read_part
from livery.footman import Failed, TimedOut
from livery.workshop import Package


class _Spawns:
    """``livery.footman.run`` faked: each program answers by its file stem, as told.

    A program it was told nothing of is not installed, so its spawn
    raises ``OSError``, as the spawn of a missing program does.
    """

    def __init__(self, answers: dict[str, tuple[int, str] | BaseException]) -> None:
        self.answers = answers
        self.calls: list[list[str]] = []
        self.inputs: list[object] = []

    def __call__(self, argv: list[str], **kwargs: object) -> SimpleNamespace:
        self.calls.append(list(argv))
        self.inputs.append(kwargs.get("input"))
        answer = self.answers.get(Path(argv[0]).stem.lower())
        if answer is None:
            raise OSError(2, "No such file or directory", argv[0])
        if isinstance(answer, BaseException):
            raise answer
        code, stdout = answer
        return SimpleNamespace(
            code=code, stdout=stdout, stderr="tool says no" if code else ""
        )

    def called(self, name: str) -> list[list[str]]:
        """The calls of the program whose file stem is *name*."""
        return [argv for argv in self.calls if Path(argv[0]).stem.lower() == name]


class _Ctest:
    """A ctest handle whose run leaves *leaves*, as an instrumented run leaves counters.

    A listing (``--show-only``) answers *listing*, an exit code and what
    it prints. A handle that is not *deployed* raises ``OSError`` on
    every call, as the spawn of a missing program does.
    """

    def __init__(
        self,
        *,
        leaves: tuple[Path, ...] = (),
        listing: tuple[int, str] = (0, '{"tests": []}'),
        deployed: bool = True,
    ) -> None:
        self.leaves = leaves
        self.listing = listing
        self.deployed = deployed
        self.runs: list[tuple[str, ...]] = []

    def opts(self, **_kwargs: object) -> Callable[..., SimpleNamespace]:
        def _call(*args: str) -> SimpleNamespace:
            if not self.deployed:
                raise OSError(2, "No such file or directory", "ctest")
            if args[0] == "--show-only=json-v1":
                code, stdout = self.listing
                return SimpleNamespace(code=code, stdout=stdout, stderr="")
            self.runs.append(args)
            for path in self.leaves:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"counters")
            return SimpleNamespace(code=0, stdout="100% tests passed", stderr="")

        return _call


class _Engine:
    """A dotnet-coverage handle: it instruments into a copy and collects *junit*."""

    def __init__(self, junit: str) -> None:
        self.junit = junit
        self.calls: list[tuple[str, ...]] = []

    def opts(self, **_kwargs: object) -> Callable[..., SimpleNamespace]:
        def _call(*args: str) -> SimpleNamespace:
            self.calls.append(args)
            if args[0] == "instrument":
                Path(args[args.index("-o") + 1]).write_bytes(b"MZ instrumented")
            else:
                verdict = Path(args[args.index("--output-junit") + 1])
                verdict.write_text(self.junit, encoding="utf-8")
            return SimpleNamespace(code=0, stdout="", stderr="")

        return _call


@pytest.fixture
def faked_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No program of the host answers, and the gate's tools run in this environment.

    PATH is an empty directory, so a measurer the host has never answers
    for a fake that is missing.
    """
    empty = tmp_path / "empty-path"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    monkeypatch.setattr(_cmake, "toolchain_env", lambda: dict(os.environ))


def _programs(directory: Path, *names: str) -> Path:
    """*directory* with an empty program for each of *names*, bare and as ``.exe``."""
    directory.mkdir(parents=True, exist_ok=True)
    for name in names:
        for program in (directory / name, directory / f"{name}.exe"):
            program.write_text("")
            program.chmod(0o755)
    return directory


def _measured(root: Path, compiler_id: str = "", compiler: str = "") -> Package:
    """A cmake member of a workspace that lists coverage, so its gate build is measured.

    Its gate build reads as configured with *compiler_id* at *compiler*,
    as CMake's compiler file names them; with neither, no configure ran.
    """
    (root / "workshop.toml").write_text('[workspace]\nextensions = ["coverage"]\n')
    directory = root / "packages" / "native"
    (directory / "src").mkdir(parents=True)
    (directory / "src" / "native.cpp").write_text("int native() {\n  return 1;\n}\n")
    (directory / "workshop.toml").write_text(
        'extensions = ["cmake", "conan"]\nname = "acme-native"\n'
    )
    build_dir = directory / _cmake.GATE_BUILD_DIR
    build_dir.mkdir(parents=True)
    named = [f'set(CMAKE_CXX_COMPILER "{compiler}")'] if compiler else []
    named += [f'set(CMAKE_CXX_COMPILER_ID "{compiler_id}")'] if compiler_id else []
    if named:
        found = build_dir / "CMakeFiles" / "4.4.2" / "CMakeCXXCompiler.cmake"
        found.parent.mkdir(parents=True)
        found.write_text("\n".join(named) + "\n")
    return Package(
        directory=directory,
        path="packages/native",
        name="acme-native",
        depends=(),
        extensions=("cmake", "conan"),
    )


def _build_dir(package: Package) -> Path:
    return package.directory / _cmake.GATE_BUILD_DIR


def _listing(*programs: Path) -> tuple[int, str]:
    """A green ctest listing with one test for each of *programs*, as its command."""
    tests = [{"name": program.stem, "command": [str(program)]} for program in programs]
    return 0, json.dumps({"tests": tests})


def _lines(root: Path) -> dict[str, dict[str, dict[int, int]]]:
    """The parts measured runs left at *root*: each file's lines by unit, unrun at 0."""
    found: dict[str, dict[str, dict[int, int]]] = {}
    for path in part_files(root):
        part = read_part(path, root)
        found[part.unit] = {
            name: {line: entry.counts.get(line, 0) for line in sorted(entry.statements)}
            for name, entry in part.files.items()
        }
    return found


def _undeployed(tool: str) -> str:
    """The workshop's refusal for *tool*, a tool of the store not on PATH."""
    from livery.workshop import undeployed

    with pytest.raises(Failed) as taught:
        undeployed(tool)
    return str(taught.value)


# A compiler without a measurer: the suite ran, and nothing passes as measured.


@pytest.mark.usefixtures("faked_host")
@pytest.mark.parametrize(
    ("compiler_id", "compiler", "named"),
    [("Intel", "/opt/intel/bin/icpx", "Intel"), ("", "", "is unknown")],
    ids=["unknown-family", "not-configured"],
)
def test_a_measured_run_built_by_a_compiler_without_a_measurer_refuses_leaving_no_part(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    compiler_id: str,
    compiler: str,
    named: str,
) -> None:
    package = _measured(tmp_path, compiler_id, compiler)
    earlier = write_part(tmp_path, package.path, {}, "gcov")
    ctest = _Ctest()
    monkeypatch.setattr(tools, "ctest", ctest)
    with pytest.raises(Failed, match=f"compiler {named}"):
        _cmake.test(package, tmp_path)
    assert ctest.runs
    assert not earlier.exists()
    assert part_files(tmp_path) == []


# gcov, beside gcc.


@pytest.mark.usefixtures("faked_host")
def test_a_gcov_run_that_left_no_counters_refuses_naming_the_configure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gcc = _programs(tmp_path / "gcc", "g++", "gcov")
    package = _measured(tmp_path, "GNU", str(gcc / "g++"))
    stale = _build_dir(package) / "CMakeFiles" / "native.dir" / "native.cpp.gcda"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"an earlier run's counters")
    monkeypatch.setattr(tools, "ctest", _Ctest())
    spawns = _Spawns({"gcov": (0, "")})
    monkeypatch.setattr(footman, "run", spawns)
    with pytest.raises(Failed, match=r"left no \.gcda counter under build/gate"):
        _cmake.test(package, tmp_path)
    # gcov adds a run's counts to the counters it finds, so the last
    # run's go before the run and never pass as this run's.
    assert not stale.exists()
    assert spawns.calls == []


@pytest.mark.usefixtures("faked_host")
def test_a_gcc_build_without_a_gcov_beside_it_or_on_path_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gcc = _programs(tmp_path / "gcc", "g++-13")
    package = _measured(tmp_path, "GNU", str(gcc / "g++-13"))
    counters = _build_dir(package) / "native.gcda"
    monkeypatch.setattr(tools, "ctest", _Ctest(leaves=(counters,)))
    monkeypatch.setattr(footman, "run", _Spawns({}))
    with pytest.raises(Failed, match="no gcov is beside it or on PATH"):
        _cmake.test(package, tmp_path)


@pytest.mark.usefixtures("faked_host")
def test_a_gcov_that_fails_on_a_counter_file_refuses_with_its_words(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gcc = _programs(tmp_path / "gcc", "g++-13", "gcov-13", "gcov")
    package = _measured(tmp_path, "GNU", str(gcc / "g++-13"))
    counters = _build_dir(package) / "native.gcda"
    monkeypatch.setattr(tools, "ctest", _Ctest(leaves=(counters,)))
    spawns = _Spawns({"gcov-13": (4, ""), "gcov": (0, "")})
    monkeypatch.setattr(footman, "run", spawns)
    with pytest.raises(Failed, match=r"gcov exited 4 on native\.gcda") as caught:
        _cmake.test(package, tmp_path)
    assert "tool says no" in str(caught.value)
    # The gcov of the compiler's own version, beside it, reads the run.
    (call,) = spawns.called("gcov-13")
    assert Path(call[0]).parent == gcc
    assert spawns.called("gcov") == []


@pytest.mark.usefixtures("faked_host")
def test_gcov_on_path_reads_every_counter_file_of_the_run_into_the_part(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    on_path = _programs(tmp_path / "path", "gcov")
    monkeypatch.setenv("PATH", str(on_path))
    # CMake named the compiler's id alone, so gcov comes from PATH.
    package = _measured(tmp_path, "GNU")
    build_dir = _build_dir(package)
    other = package.directory / "src" / "other.cpp"
    other.write_text("int other() {\n  return 2;\n}\n")
    leaves = (build_dir / "a" / "native.gcda", build_dir / "b" / "native.gcda")
    monkeypatch.setattr(tools, "ctest", _Ctest(leaves=leaves))
    printed = json.dumps(
        {
            "files": [
                {
                    # Named relative to the build directory, as the
                    # compile line named it.
                    "file": "../../src/native.cpp",
                    "lines": [
                        {"line_number": 1, "count": 1},
                        {"line_number": 2, "count": 0},
                    ],
                },
                {"file": str(other), "lines": [{"line_number": 2, "count": 1}]},
            ]
        }
    )
    spawns = _Spawns({"gcov": (0, printed)})
    monkeypatch.setattr(footman, "run", spawns)
    _cmake.test(package, tmp_path)
    # Each counter file names both sources, and their counts add up.
    assert _lines(tmp_path) == {
        "packages/native": {
            "packages/native/src/native.cpp": {1: 2, 2: 0},
            "packages/native/src/other.cpp": {2: 2},
        }
    }
    reads = spawns.called("gcov")
    assert [Path(call[-1]) for call in reads] == list(leaves)
    assert all(Path(call[0]).parent == on_path for call in reads)


# llvm-cov and llvm-profdata, beside clang.


@pytest.mark.usefixtures("faked_host")
def test_an_llvm_run_that_left_no_profiles_refuses_naming_the_configure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    llvm = _programs(tmp_path / "llvm", "clang++", "llvm-profdata", "llvm-cov")
    package = _measured(tmp_path, "Clang", str(llvm / "clang++"))
    monkeypatch.setattr(tools, "ctest", _Ctest())
    spawns = _Spawns({"llvm-profdata": (0, ""), "llvm-cov": (0, "")})
    monkeypatch.setattr(footman, "run", spawns)
    with pytest.raises(Failed, match=r"left no \.profraw under build/gate/coverage"):
        _cmake.test(package, tmp_path)
    assert spawns.calls == []


@pytest.mark.usefixtures("faked_host")
@pytest.mark.parametrize("host", ["linux", "darwin"])
def test_a_clang_build_without_its_llvm_tools_anywhere_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, host: str
) -> None:
    llvm = _programs(tmp_path / "llvm", "clang++")
    package = _measured(tmp_path, "AppleClang", str(llvm / "clang++"))
    profile = _build_dir(package) / _cmake.PROFILE_DIR / "1-0.profraw"
    monkeypatch.setattr(tools, "ctest", _Ctest(leaves=(profile,)))
    monkeypatch.setattr(_cmake, "sys", SimpleNamespace(platform=host))
    # xcrun is not installed either: its spawn raises.
    spawns = _Spawns({})
    monkeypatch.setattr(footman, "run", spawns)
    with pytest.raises(Failed, match="is not beside it, on PATH, or where xcrun looks"):
        _cmake.test(package, tmp_path)
    # Only macOS has an SDK for xcrun to look in.
    assert bool(spawns.called("xcrun")) == (host == "darwin")


@pytest.mark.usefixtures("faked_host")
def test_an_llvm_profdata_that_fails_to_merge_refuses_with_its_words(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    llvm = _programs(tmp_path / "llvm", "clang++", "llvm-profdata", "llvm-cov")
    package = _measured(tmp_path, "Clang", str(llvm / "clang++"))
    profile = _build_dir(package) / _cmake.PROFILE_DIR / "1-0.profraw"
    monkeypatch.setattr(tools, "ctest", _Ctest(leaves=(profile,)))
    spawns = _Spawns({"llvm-profdata": (1, ""), "llvm-cov": (0, "")})
    monkeypatch.setattr(footman, "run", spawns)
    with pytest.raises(Failed, match="llvm-profdata exited 1") as caught:
        _cmake.test(package, tmp_path)
    assert "tool says no" in str(caught.value)
    (merge,) = spawns.called("llvm-profdata")
    assert merge[1:3] == ["merge", "-sparse"]
    assert str(profile) in merge
    assert spawns.called("llvm-cov") == []


@pytest.mark.usefixtures("faked_host")
@pytest.mark.parametrize(
    "listing",
    [
        (1, ""),
        (0, "not json"),
        (
            0,
            json.dumps(
                {"tests": ["a name", {"name": "t"}, {"command": []}, {"command": "t"}]}
            ),
        ),
        (0, json.dumps({"tests": [{"command": ["/nowhere/test_native"]}]})),
    ],
    ids=["listing-failed", "listing-not-json", "no-command", "no-such-program"],
)
def test_an_llvm_run_whose_ctest_names_no_test_executable_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, listing: tuple[int, str]
) -> None:
    llvm = _programs(tmp_path / "llvm", "clang++", "llvm-profdata", "llvm-cov")
    package = _measured(tmp_path, "Clang", str(llvm / "clang++"))
    profile = _build_dir(package) / _cmake.PROFILE_DIR / "1-0.profraw"
    monkeypatch.setattr(tools, "ctest", _Ctest(leaves=(profile,), listing=listing))
    spawns = _Spawns({"llvm-profdata": (0, ""), "llvm-cov": (0, "")})
    monkeypatch.setattr(footman, "run", spawns)
    with pytest.raises(Failed, match="ctest names no test executable to read coverage"):
        _cmake.test(package, tmp_path)
    assert spawns.called("llvm-cov") == []


@pytest.mark.usefixtures("faked_host")
def test_llvm_cov_reads_every_test_executable_and_library_and_refuses_when_it_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    llvm = _programs(tmp_path / "llvm", "clang++", "llvm-profdata", "llvm-cov")
    package = _measured(tmp_path, "Clang", str(llvm / "clang++"))
    build_dir = _build_dir(package)
    native, other = build_dir / "test_native", build_dir / "test_other"
    library = build_dir / "lib" / "libnative.so"
    library.parent.mkdir()
    for built in (native, other, library):
        built.write_bytes(b"\x7fELF")
    profile = build_dir / _cmake.PROFILE_DIR / "1-0.profraw"
    # Two tests run one executable: it is read once.
    listing = _listing(native, native, other)
    monkeypatch.setattr(tools, "ctest", _Ctest(leaves=(profile,), listing=listing))
    spawns = _Spawns({"llvm-profdata": (0, ""), "llvm-cov": (2, "")})
    monkeypatch.setattr(footman, "run", spawns)
    with pytest.raises(Failed, match="llvm-cov exited 2") as caught:
        _cmake.test(package, tmp_path)
    assert "tool says no" in str(caught.value)
    (export,) = spawns.called("llvm-cov")
    assert export[1:3] == ["export", "-format=text"]
    assert export[3].startswith("-instr-profile=")
    assert export[3].endswith("merged.profdata")
    assert export[4:] == [str(native), "-object", str(other), "-object", str(library)]


def _llvm_export(source: Path) -> str:
    """llvm-cov's JSON export of *source*: line 1 ran once, in ``native()``, mangled."""
    return json.dumps(
        {
            "data": [
                {
                    "files": [
                        {
                            "filename": str(source),
                            "segments": [
                                [1, 1, 1, True, True, False],
                                [1, 20, 0, False, False, False],
                            ],
                            "branches": [],
                        }
                    ],
                    "functions": [
                        {
                            "name": "_Z6nativev",
                            "count": 1,
                            "filenames": [str(source)],
                            "regions": [[1, 1, 1, 20, 1, 0, 0, 0]],
                        }
                    ],
                }
            ]
        }
    )


@pytest.mark.usefixtures("faked_host")
@pytest.mark.parametrize(
    ("demangler", "answer", "shown"),
    [
        ("", (0, ""), "_Z6nativev"),
        ("c++filt", (1, ""), "_Z6nativev"),
        ("llvm-cxxfilt", (0, "native()\n"), "native()"),
    ],
    ids=["no-demangler", "demangler-failed", "demangled"],
)
def test_llvm_cov_s_functions_keep_their_mangled_names_without_a_working_demangler(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    demangler: str,
    answer: tuple[int, str],
    shown: str,
) -> None:
    tools_beside = ("clang++", "llvm-profdata", "llvm-cov")
    llvm = _programs(
        tmp_path / "llvm", *tools_beside, *([demangler] if demangler else [])
    )
    package = _measured(tmp_path, "Clang", str(llvm / "clang++"))
    build_dir = _build_dir(package)
    native = build_dir / "test_native"
    native.write_bytes(b"\x7fELF")
    profile = build_dir / _cmake.PROFILE_DIR / "1-0.profraw"
    monkeypatch.setattr(
        tools, "ctest", _Ctest(leaves=(profile,), listing=_listing(native))
    )
    # No SDK for xcrun to look in: the demangler is the one beside clang.
    monkeypatch.setattr(_cmake, "sys", SimpleNamespace(platform="linux"))
    answers: dict[str, tuple[int, str] | BaseException] = {
        "llvm-profdata": (0, ""),
        "llvm-cov": (0, _llvm_export(package.directory / "src" / "native.cpp")),
    }
    if demangler:
        answers[demangler] = answer
    spawns = _Spawns(answers)
    monkeypatch.setattr(footman, "run", spawns)
    _cmake.test(package, tmp_path)
    (written,) = part_files(tmp_path)
    entry = read_part(written, tmp_path).files["packages/native/src/native.cpp"]
    assert [region.name for region in entry.regions] == [shown]
    assert dict(entry.counts) == {1: 1}
    if demangler:
        # The demangler reads the names on its standard input.
        (call,) = spawns.called(demangler)
        assert spawns.inputs[spawns.calls.index(call)] == "_Z6nativev\n"


# Microsoft's engine, beside MSVC.


def _msvc(tmp_path: Path) -> tuple[Package, Path]:
    """A measured package whose gate build says MSVC, and its one test executable."""
    package = _measured(tmp_path, "MSVC", "C:/VS/VC/Tools/MSVC/14.51/bin/cl.exe")
    native = _build_dir(package) / "test_native.exe"
    native.write_bytes(b"MZ original")
    return package, native


@pytest.mark.usefixtures("faked_host")
def test_the_msvc_measurer_refuses_when_ctest_cannot_list_the_suite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package, native = _msvc(tmp_path)
    monkeypatch.setattr(tools, "ctest", _Ctest(deployed=False))
    engine = _Engine(junit="")
    monkeypatch.setattr(tools, "dotnet_coverage", engine, raising=False)
    with pytest.raises(Failed) as caught:
        _cmake.test(package, tmp_path)
    assert str(caught.value) == _undeployed("ctest")
    assert engine.calls == []
    assert native.read_bytes() == b"MZ original"


@pytest.mark.usefixtures("faked_host")
def test_the_msvc_measurer_refuses_when_no_ctest_is_on_path_for_the_collector(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package, native = _msvc(tmp_path)
    monkeypatch.setattr(tools, "ctest", _Ctest(listing=_listing(native)))
    engine = _Engine(junit="")
    monkeypatch.setattr(tools, "dotnet_coverage", engine, raising=False)
    with pytest.raises(Failed) as caught:
        _cmake.test(package, tmp_path)
    assert str(caught.value) == _undeployed("ctest")
    assert engine.calls == []
    assert native.read_bytes() == b"MZ original"


@pytest.mark.usefixtures("faked_host")
def test_the_msvc_measurer_refuses_a_ctest_report_that_is_not_xml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _programs(tmp_path / "store", "ctest")
    monkeypatch.setenv("PATH", str(store))
    package, native = _msvc(tmp_path)
    monkeypatch.setattr(tools, "ctest", _Ctest(listing=_listing(native)))
    engine = _Engine(junit="<testsuite")
    monkeypatch.setattr(tools, "dotnet_coverage", engine, raising=False)
    with pytest.raises(Failed, match="ctest's report is not XML"):
        _cmake.test(package, tmp_path)
    instrument, collect = engine.calls
    assert instrument[0] == "instrument"
    # The collector spawns the store's ctest by its path.
    assert collect[0] == "collect"
    assert Path(collect[collect.index("--") + 1]).parent == store
    assert native.read_bytes() == b"MZ original"
    assert part_files(tmp_path) == []


# The toolchain environment on Windows, with vswhere and cmd.exe faked.


@pytest.fixture
def windows_without_a_compiler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    """A Windows x64 host with vswhere installed and no compiler; vswhere's path.

    No compiler is chosen and none is on PATH. The toolchain environment
    is read afresh by the test, and by the next.
    """
    vswhere = _cmake.vswhere_path({"PROGRAMFILES(X86)": str(tmp_path / "pf")})
    vswhere.parent.mkdir(parents=True)
    vswhere.write_text("")
    monkeypatch.setattr(_cmake, "_on_windows", lambda: True)
    monkeypatch.setattr(platform, "machine", lambda: "AMD64")
    monkeypatch.delenv("CXX", raising=False)
    monkeypatch.delenv("CC", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "bare"))
    monkeypatch.setenv("PROGRAMFILES(X86)", str(tmp_path / "pf"))
    _cmake._entered.cache_clear()
    yield vswhere
    _cmake._entered.cache_clear()


def test_entering_msvc_refuses_when_vswhere_will_not_run(
    windows_without_a_compiler: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawns = _Spawns({})
    monkeypatch.setattr(footman, "run", spawns)
    with pytest.raises(Failed, match=r"no Visual Studio installation has the C\+\+"):
        _cmake.toolchain_env()
    (asked,) = spawns.calls
    assert asked[0] == str(windows_without_a_compiler)


@pytest.mark.parametrize(
    "shell",
    [
        OSError(2, "No such file or directory", "cmd.exe"),
        TimedOut("enter the C++ build tools", 120.0),
        (1, ""),
    ],
    ids=["shell-missing", "shell-timed-out", "shell-failed"],
)
def test_entering_msvc_refuses_when_its_batch_file_cannot_be_run(
    windows_without_a_compiler: Path,
    monkeypatch: pytest.MonkeyPatch,
    shell: tuple[int, str] | BaseException,
) -> None:
    spawns = _Spawns({"vswhere": (0, "C:\\VS\n"), "cmd": shell})
    monkeypatch.setattr(footman, "run", spawns)
    with pytest.raises(Failed, match="left no VCToolsInstallDir"):
        _cmake.toolchain_env()
    (entered,) = spawns.called("cmd")
    assert entered[1:3] == ["/d", "/c"]


def test_entering_msvc_runs_the_tools_batch_file_and_reads_back_its_environment(
    windows_without_a_compiler: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scripts: list[str] = []

    def _run(argv: list[str], **_kwargs: object) -> SimpleNamespace:
        if Path(argv[0]).stem == "vswhere":
            return SimpleNamespace(code=0, stdout="C:\\VS\r\n", stderr="")
        # The batch file lives only while cmd.exe runs it.
        scripts.append(Path(argv[-1]).read_text(encoding="utf-8"))
        printed = "VCToolsInstallDir=C:\\VS\\VC\\Tools\\\nPath=C:\\VS\\bin\n"
        return SimpleNamespace(code=0, stdout=printed, stderr="")

    monkeypatch.setattr(footman, "run", _run)
    env = _cmake.toolchain_env()
    assert env["VCTOOLSINSTALLDIR"] == "C:\\VS\\VC\\Tools\\"
    assert env["PATH"] == "C:\\VS\\bin"
    assert (env["CC"], env["CXX"]) == ("cl", "cl")
    (script,) = scripts
    assert "vcvars64.bat" in script
    assert script.endswith('" >nul\n@set\n')
