"""The Windows coverage spike: one program, every measurer to hand, each answer printed.

Lives on the branch that carries it and nowhere else. It decides how
a cpp-conan package built with MSVC or with clang on Windows is
measured: one small program is built with ``cl`` and with
``clang-cl``, the MSVC binary runs under Microsoft's engines, the
deprecated ``CodeCoverage.exe``, the ``dotnet-coverage`` tool, and
under OpenCppCoverage, the clang binaries run under ``llvm-profdata``
and ``llvm-cov`` with the MSVC linker and with lld, and every tool's
per-line answer for the program's source is printed. Each step prints
its exit code and output and nothing raises, so one run shows every
arm.
"""

from __future__ import annotations

import os
import platform
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path

import livery.footman as footman

SOURCE = """#include <cstdio>

int pick(int n) {
    if (n > 3) {
        return n * 2;
    }
    return n - 1;
}

int never(int n) {
    return n + 42;
}

int main() {
    std::printf("%d\\n", pick(5));
    return 0;
}
"""

NUGET = "https://www.nuget.org/api/v2/package/Microsoft.CodeCoverage"
OCC = (
    "https://github.com/OpenCppCoverage/OpenCppCoverage/releases/download/"
    "release-0.9.9.0/OpenCppCoverageSetup-x64-0.9.9.0.exe"
)
VSWHERE = r"C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe"
LLVM = r"C:\Program Files\LLVM"
SEVEN_ZIP = r"C:\Program Files\7-Zip\7z.exe"
#: The OS shell, which reads the vcvars batch file; no store handle exists
#: for it, and the spike alone spawns it.
SHELL = "cmd.exe"


def _say(title: str) -> None:
    print(f"\n=== {title}")


def _run(
    argv: list[str],
    *,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
    encoding: str = "utf-8",
    lines: int = 60,
) -> tuple[int, str]:
    """Run *argv*, print its exit code and output, return both; never raises."""
    result = footman.run(
        argv, nofail=True, recorded=False, env=env, cwd=cwd, encoding=encoding
    )
    code = int(result)
    text = f"{result.stdout}{result.stderr}".replace("\ufeff", "").strip()
    print(f"  $ {' '.join(argv)}\n  exit {code}")
    if text:
        shown = [line for line in text.splitlines() if line.strip()]
        print("  " + "\n  ".join(shown[:lines]))
    return code, text


def _vs_root() -> str:
    if not Path(VSWHERE).is_file():
        print(f"  {VSWHERE}: absent")
        return ""
    code, text = _run(
        [VSWHERE, "-latest", "-products", "*", "-property", "installationPath"]
    )
    return text.splitlines()[-1].strip() if code == 0 and text else ""


def _msvc_env(vs: str, work: Path) -> dict[str, str]:
    """The environment vcvars64.bat leaves, read back through ``set``."""
    env = dict(os.environ)
    batch = work / "spike-env.cmd"
    batch.write_text(
        f'@call "{vs}\\VC\\Auxiliary\\Build\\vcvars64.bat" >nul\n@set\n',
        encoding="utf-8",
    )
    result = footman.run([SHELL, "/d", "/c", str(batch)], nofail=True, recorded=False)
    if int(result) != 0:
        print(f"  vcvars64.bat exited {int(result)}: {result.stderr.strip()[:300]}")
        return env
    for line in result.stdout.splitlines():
        key, sep, value = line.partition("=")
        if sep and key and not key.startswith("="):
            env[key] = value
    print(
        f"  vcvars: {len(env)} variables;"
        f" VCToolsVersion={env.get('VCToolsVersion', '?')}"
    )
    return env


def _build_msvc(env: dict[str, str], work: Path) -> Path | None:
    cl = shutil.which("cl", path=env.get("PATH", ""))
    if not cl:
        print("  cl.exe is not on the vcvars PATH")
        return None
    code, _ = _run(
        [
            cl,
            "/nologo",
            "/Zi",
            "/EHsc",
            "/Od",
            "/Fe:spike-msvc.exe",
            "spike.cpp",
            "/link",
            "/DEBUG",
            "/PROFILE",
        ],
        env=env,
        cwd=work,
    )
    exe = work / "spike-msvc.exe"
    return exe if code == 0 and exe.is_file() else None


def _nuget_engine(work: Path) -> Path | None:
    """Microsoft's engine from the latest Microsoft.CodeCoverage package."""
    target = work / "codecoverage.nupkg"
    try:
        urllib.request.urlretrieve(NUGET, target)
    except OSError as exc:
        print(f"  download failed: {exc}")
        return None
    print(f"  downloaded {target.stat().st_size} bytes")
    with zipfile.ZipFile(target) as archive:
        names = archive.namelist()
        engines = [n for n in names if n.lower().endswith("codecoverage.exe")]
        spec = next((n for n in names if n.endswith(".nuspec")), "")
        if spec:
            text = archive.read(spec).decode("utf-8", errors="replace")
            version = next(
                (line.strip() for line in text.splitlines() if "<version>" in line),
                "?",
            )
            print(f"  package {version}")
        executables = [n for n in names if n.lower().endswith((".exe", ".dll"))]
        print("  executables in the package:\n    " + "\n    ".join(executables[:40]))
        archive.extractall(work / "nuget")
    chosen = next(
        (n for n in engines if "amd64" in n.lower()), engines[0] if engines else ""
    )
    return (work / "nuget" / chosen) if chosen else None


def _vs_engine(vs: str) -> Path | None:
    home = Path(vs) / "Team Tools" / "Dynamic Code Coverage Tools"
    found = sorted(home.rglob("CodeCoverage.exe")) if home.is_dir() else []
    print(f"  {home}: " + (", ".join(str(p) for p in found) or "no engine"))
    return next(
        (p for p in found if "amd64" in str(p).lower()), found[0] if found else None
    )


def _print_xml(xml: Path, keep: tuple[str, ...]) -> bool:
    """Print the lines of *xml* about the program; whether any carries line data."""
    if not xml.is_file():
        print(f"  no {xml.name} written")
        return False
    text = xml.read_text(encoding="utf-8", errors="replace")
    about = [
        line.strip()
        for line in text.splitlines()
        if "spike" in line.lower() or any(mark in line for mark in keep)
    ]
    print(f"  {xml.name}: {len(text.splitlines())} lines; the ones about the program:")
    print("  " + "\n  ".join(about[:80]))
    return any(mark in line for line in about for mark in keep)


def _measure_engine(engine: Path, exe: Path, tag: str, work: Path) -> bool:
    """Collect with the deprecated engine and analyze to XML; its usage first."""
    coverage = work / f"spike-{tag}.coverage"
    xml = work / f"spike-{tag}.xml"
    _run([str(engine)], encoding="utf-16-le", lines=80)
    _run(
        [str(engine), "collect", f"/output:{coverage}", str(exe)],
        cwd=work,
        encoding="utf-16-le",
        lines=80,
    )
    if not coverage.is_file():
        print(f"  no {coverage.name} written")
        return False
    print(f"  {coverage.name}: {coverage.stat().st_size} bytes")
    _run(
        [str(engine), "analyze", f"/output:{xml}", str(coverage)],
        cwd=work,
        encoding="utf-16-le",
    )
    return _print_xml(xml, ("<range", "<source_file"))


def _dotnet_coverage(env: dict[str, str], exe: Path, work: Path) -> bool:
    """Collect with dotnet-coverage, dynamically and after static instrumentation."""
    dotnet = shutil.which("dotnet", path=env.get("PATH", ""))
    if not dotnet:
        print("  dotnet is not on PATH")
        return False
    _run([dotnet, "--version"])
    tools = str(Path(env.get("USERPROFILE", "")) / ".dotnet" / "tools")
    _run([dotnet, "tool", "install", "--global", "dotnet-coverage"], env=env)
    run_env = dict(env)
    run_env["PATH"] = tools + os.pathsep + env.get("PATH", "")
    tool = shutil.which("dotnet-coverage", path=run_env["PATH"])
    if not tool:
        print(f"  dotnet-coverage did not land in {tools}")
        return False
    _run([tool, "--version"], env=run_env)
    dynamic = work / "spike-dotnet-dynamic.xml"
    _run(
        [tool, "collect", "-f", "cobertura", "-o", str(dynamic), "--", str(exe)],
        env=run_env,
        cwd=work,
    )
    got = _print_xml(dynamic, ("<line ", "<class "))
    instrumented = work / "spike-msvc-instrumented.exe"
    _run(
        [tool, "instrument", "-o", str(instrumented), str(exe)],
        env=run_env,
        cwd=work,
    )
    if instrumented.is_file():
        static = work / "spike-dotnet-static.xml"
        _run(
            [
                tool,
                "collect",
                "-f",
                "cobertura",
                "-o",
                str(static),
                "--",
                str(instrumented),
            ],
            env=run_env,
            cwd=work,
        )
        got = _print_xml(static, ("<line ", "<class ")) or got
    return got


def _open_cpp_coverage(exe: Path, work: Path) -> bool:
    """Collect with OpenCppCoverage, its installer unpacked by 7-Zip or run silently."""
    installer = work / "OpenCppCoverageSetup.exe"
    try:
        urllib.request.urlretrieve(OCC, installer)
    except OSError as exc:
        print(f"  download failed: {exc}")
        return False
    print(f"  downloaded {installer.stat().st_size} bytes")
    home = work / "occ"
    if Path(SEVEN_ZIP).is_file():
        _run([SEVEN_ZIP, "x", "-y", f"-o{home}", str(installer)], cwd=work, lines=12)
    found = sorted(home.rglob("OpenCppCoverage.exe")) if home.is_dir() else []
    if not found:
        _run([str(installer), "/S", f"/D={home}"], cwd=work)
        found = sorted(home.rglob("OpenCppCoverage.exe")) if home.is_dir() else []
    print("  OpenCppCoverage.exe: " + (", ".join(str(p) for p in found) or "not found"))
    if not found:
        return False
    xml = work / "spike-occ.xml"
    _run(
        [
            str(found[0]),
            f"--export_type=cobertura:{xml}",
            f"--sources={work}",
            "--",
            str(exe),
        ],
        cwd=work,
    )
    return _print_xml(xml, ("<line ", "<class "))


def _measure_llvm(
    bin_dir: Path,
    env: dict[str, str],
    work: Path,
    tag: str,
    *,
    extra: tuple[str, ...] = (),
) -> bool:
    """Build with clang-cl under the MSVC environment and read llvm-cov's lines."""
    clang_cl = bin_dir / "clang-cl.exe"
    profdata = bin_dir / "llvm-profdata.exe"
    cov = bin_dir / "llvm-cov.exe"
    for tool in (clang_cl, profdata, cov):
        print(f"  {tool}: {'present' if tool.is_file() else 'absent'}")
    if not all(tool.is_file() for tool in (clang_cl, profdata, cov)):
        return False
    _run([str(clang_cl), "--version"], lines=2)
    runtime = sorted(bin_dir.parent.rglob("clang_rt.profile-x86_64.lib"))
    print(
        "  profile runtime: "
        + (str(runtime[0]) if runtime else f"not found under {bin_dir.parent}")
    )
    exe = work / f"spike-{tag}.exe"
    code, _ = _run(
        [
            str(clang_cl),
            "/Zi",
            "/EHsc",
            "/Od",
            "-fprofile-instr-generate",
            "-fcoverage-mapping",
            *extra,
            f"/Fe:{exe.name}",
            "spike.cpp",
        ],
        env=env,
        cwd=work,
    )
    if code != 0 or not exe.is_file():
        return False
    raw = work / f"spike-{tag}.profraw"
    run_env = dict(env)
    run_env["LLVM_PROFILE_FILE"] = str(raw)
    _run([str(exe)], env=run_env, cwd=work)
    if not raw.is_file():
        print("  no profraw written")
        return False
    print(f"  {raw.name}: {raw.stat().st_size} bytes")
    merged = work / f"spike-{tag}.profdata"
    _run([str(profdata), "merge", "-sparse", str(raw), "-o", str(merged)], cwd=work)
    code, text = _run(
        [str(cov), "export", "-format=lcov", f"-instr-profile={merged}", str(exe)],
        cwd=work,
    )
    return code == 0 and "DA:" in text


def spike() -> None:
    """Run every arm of the spike and print which ones gave per-line data."""
    if platform.system() != "Windows":
        print(f"  the spike runs on Windows; this is {platform.system()}")
        return
    work = Path(tempfile.mkdtemp(prefix="spike-"))
    (work / "spike.cpp").write_text(SOURCE, encoding="utf-8")
    print(f"  working in {work}")
    verdicts: dict[str, bool] = {}

    _say("Visual Studio")
    vs = _vs_root()
    print(f"  installation: {vs or 'none found'}")
    env = _msvc_env(vs, work) if vs else dict(os.environ)

    _say("MSVC build")
    msvc = _build_msvc(env, work) if vs else None
    if msvc is not None:
        _run([str(msvc)], cwd=work)

    _say("the deprecated engine from nuget.org")
    engine = _nuget_engine(work)
    if engine is not None and msvc is not None:
        verdicts["msvc + CodeCoverage.exe (nuget)"] = _measure_engine(
            engine, msvc, "nuget", work
        )

    _say("the deprecated engine from the Visual Studio install")
    engine = _vs_engine(vs) if vs else None
    if engine is not None and msvc is not None:
        verdicts["msvc + CodeCoverage.exe (visual studio)"] = _measure_engine(
            engine, msvc, "vs", work
        )

    _say("dotnet-coverage")
    if msvc is not None:
        verdicts["msvc + dotnet-coverage"] = _dotnet_coverage(env, msvc, work)

    _say("OpenCppCoverage")
    if msvc is not None:
        verdicts["msvc + OpenCppCoverage"] = _open_cpp_coverage(msvc, work)

    _say("clang-cl from the runner's LLVM, linked by link.exe")
    verdicts["clang-cl (llvm) + link.exe + llvm-cov"] = _measure_llvm(
        Path(LLVM) / "bin", env, work, "clang"
    )

    _say("clang-cl from the runner's LLVM, linked by lld-link")
    verdicts["clang-cl (llvm) + lld-link + llvm-cov"] = _measure_llvm(
        Path(LLVM) / "bin", env, work, "clang-lld", extra=("-fuse-ld=lld-link",)
    )

    _say("clang-cl bundled with Visual Studio, linked by link.exe")
    if vs:
        verdicts["clang-cl (visual studio) + link.exe + llvm-cov"] = _measure_llvm(
            Path(vs) / "VC" / "Tools" / "Llvm" / "x64" / "bin", env, work, "vsclang"
        )

    _say("summary")
    for arm, ok in verdicts.items():
        print(f"  {arm}: {'per-line data' if ok else 'nothing usable'}")
    if not verdicts:
        print("  no arm ran")
