"""The Windows coverage spike: one program, measured four ways, each answer printed.

Lives on the branch that carries it and nowhere else. It decides how
a cpp-conan package built with MSVC or with clang on Windows is
measured: one small program is built with ``cl`` and with
``clang-cl``, the MSVC binary runs under Microsoft's Code Coverage
engine taken from the NuGet package and from the Visual Studio
install, the clang binary runs under ``llvm-profdata`` and
``llvm-cov``, and every tool's per-line answer for the program's
source is printed. Each step prints its exit code and output and
nothing raises, so one run shows every arm.
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
VSWHERE = r"C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe"
LLVM = r"C:\Program Files\LLVM"
#: The OS shell, which reads the vcvars batch file; no store handle exists
#: for it, and the spike alone spawns it.
SHELL = "cmd.exe"


def _say(title: str) -> None:
    print(f"\n=== {title}")


def _run(
    argv: list[str], *, env: dict[str, str] | None = None, cwd: Path | None = None
) -> tuple[int, str]:
    """Run *argv*, print its exit code and output, return both; never raises."""
    result = footman.run(argv, nofail=True, recorded=False, env=env, cwd=cwd)
    code = int(result)
    text = f"{result.stdout}{result.stderr}".strip()
    print(f"  $ {' '.join(argv)}\n  exit {code}")
    if text:
        print("  " + "\n  ".join(text.splitlines()[:60]))
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
        print("  engines in the package: " + (", ".join(engines) or "none"))
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


def _measure_ms(engine: Path, exe: Path, tag: str, work: Path) -> bool:
    """Collect with Microsoft's engine and analyze to XML; whether lines came out."""
    coverage = work / f"spike-{tag}.coverage"
    xml = work / f"spike-{tag}.xml"
    _run([str(engine), "collect", f"/output:{coverage}", str(exe)], cwd=work)
    if not coverage.is_file():
        print(f"  no {coverage.name} written")
        return False
    print(f"  {coverage.name}: {coverage.stat().st_size} bytes")
    _run([str(engine), "analyze", f"/output:{xml}", str(coverage)], cwd=work)
    if not xml.is_file():
        print(f"  no {xml.name} written")
        return False
    text = xml.read_text(encoding="utf-8", errors="replace")
    about = [
        line.strip()
        for line in text.splitlines()
        if "spike" in line.lower()
        or "<range" in line
        or "<source_file" in line
        or "<function" in line
        or "<module " in line
    ]
    print(f"  {xml.name}: {len(text.splitlines())} lines; the ones about the program:")
    print("  " + "\n  ".join(about[:80]))
    return any("<range" in line for line in about)


def _measure_llvm(bin_dir: Path, env: dict[str, str], work: Path, tag: str) -> bool:
    """Build with clang-cl under the MSVC environment and read llvm-cov's lines."""
    clang_cl = bin_dir / "clang-cl.exe"
    profdata = bin_dir / "llvm-profdata.exe"
    cov = bin_dir / "llvm-cov.exe"
    for tool in (clang_cl, profdata, cov):
        print(f"  {tool}: {'present' if tool.is_file() else 'absent'}")
    if not all(tool.is_file() for tool in (clang_cl, profdata, cov)):
        return False
    _run([str(clang_cl), "--version"])
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
    merged = work / f"spike-{tag}.profdata"
    _run([str(profdata), "merge", "-sparse", str(raw), "-o", str(merged)], cwd=work)
    code, text = _run(
        [str(cov), "export", "-format=lcov", f"-instr-profile={merged}", str(exe)],
        cwd=work,
    )
    _run([str(cov), "report", f"-instr-profile={merged}", str(exe)], cwd=work)
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

    _say("Microsoft engine from nuget.org")
    engine = _nuget_engine(work)
    if engine is not None and msvc is not None:
        verdicts["msvc + engine (nuget)"] = _measure_ms(engine, msvc, "nuget", work)

    _say("Microsoft engine from the Visual Studio install")
    engine = _vs_engine(vs) if vs else None
    if engine is not None and msvc is not None:
        verdicts["msvc + engine (visual studio)"] = _measure_ms(
            engine, msvc, "vs", work
        )

    _say("clang-cl from the runner's LLVM")
    verdicts["clang-cl (llvm) + llvm-cov"] = _measure_llvm(
        Path(LLVM) / "bin", env, work, "clang"
    )

    _say("clang-cl bundled with Visual Studio")
    if vs:
        verdicts["clang-cl (visual studio) + llvm-cov"] = _measure_llvm(
            Path(vs) / "VC" / "Tools" / "Llvm" / "x64" / "bin", env, work, "vsclang"
        )

    _say("summary")
    for arm, ok in verdicts.items():
        print(f"  {arm}: {'per-line data' if ok else 'nothing usable'}")
    if not verdicts:
        print("  no arm ran")
