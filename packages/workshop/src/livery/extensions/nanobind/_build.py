"""Build a nanobind package's platform wheels and its sdist.

The build phase's step for a package whose set holds nanobind:
cibuildwheel builds and repairs each wheel, so even one host's wheel is
manylinux-compliant, and ``uv build`` writes the sdist. The release
train runs the step on the build phase's isolated side. nanobind's
declaration deletes python's build step in the same set, since
``uv build`` would write a pure wheel. The floor legs build a second
set of wheels through `build_wheels`, which leaves the collected
``dist/`` alone; the conan environment each build takes is the
``_container`` module's.
"""

from __future__ import annotations

import os
import shutil
import sys
import sysconfig
from pathlib import Path
from typing import TYPE_CHECKING, cast

import livery.toolroom.tools as tools
from livery.extensions.nanobind import _container
from livery.extensions.python import _backend as _python
from livery.footman import fail

if TYPE_CHECKING:
    from livery.workshop import PhaseContext
    from livery.workshop._packages import Package

#: What ``uv tool run`` resolves for the build. A floor and a cap,
#: not an exact pin: the run has no lockfile to consult, and a major
#: bump changes cibuildwheel's defaults deliberately. The release
#: matrix (its own phase) pins its legs exactly.
CIBUILDWHEEL = "cibuildwheel>=3.0,<4"


#: What a package passes to conan install when its own pyproject
#: names nothing: build what the cache lacks, leave the rest alone.
DEFAULT_INSTALL_ARGS = "--build=missing"


def build(ctx: PhaseContext) -> None:
    """Build the package's platform wheels and its sdist, at the engine's epoch."""
    epoch = cast("int", ctx.read("epoch"))
    ctx.provide("dist", build_dist(ctx.member, ctx.root, epoch=epoch))


def build_dist(package: Package, root: Path, *, epoch: int = 0) -> Path:
    """Build the platform wheels through cibuildwheel, and the sdist; the ``dist/``.

    Locally the run pins to the running interpreter
    (``CIBW_BUILD``, respected when the caller already set it), so
    the build stays minutes; the release matrix widens the set in
    CI. Always from a clean ``dist/``, like python's build.

    Raises:
        Failed: when cibuildwheel or ``uv build --sdist`` exits
            non-zero, or a wheel carries no platform tag
            (`assert_platform_tagged`).
    """
    dist = package.directory / "dist"
    shutil.rmtree(dist, ignore_errors=True)
    env = build_wheels(package, root, dist, epoch=epoch)
    sdist = tools.uv.opts(cwd=package.directory, env=env, nofail=True, recorded=False)(
        "build", "--sdist", "--out-dir", str(dist)
    )
    if sdist.code != 0:
        fail(
            f"uv build --sdist ({package.name}) exited {sdist.code}:\n"
            f"{sdist.stdout[-4000:]}{sdist.stderr[-2000:]}"
        )
    return dist


def conan_install_args(package: Package) -> str:
    """What the package's ``pyproject.toml`` passes to conan install.

    Reads ``[tool.scikit-build.cmake.define] CONAN_INSTALL_ARGS``,
    the package's own declaration, in either spelling: a plain
    string, or the table whose ``default`` the environment
    overrides. The floor leg starts from this and adds its pin, so
    the two builds differ by the pin alone.
    """
    import tomllib

    pyproject = package.directory / "pyproject.toml"
    if not pyproject.is_file():
        return DEFAULT_INSTALL_ARGS
    data = tomllib.loads(pyproject.read_text("utf-8"))
    tool = data.get("tool", {})
    define = tool.get("scikit-build", {}).get("cmake", {}).get("define", {})
    declared = define.get("CONAN_INSTALL_ARGS", "")
    if isinstance(declared, dict):
        return str(declared.get("default", "")) or DEFAULT_INSTALL_ARGS
    return str(declared) or DEFAULT_INSTALL_ARGS


def assert_platform_tagged(package: Package, dist: Path) -> None:
    """Refuse any pure-tagged wheel in *dist*; the identity guard.

    A nanobind package's wheel that says ``none-any`` was built
    without its extension module, and publishing it would hand every
    consumer an ImportError. The other half of the guard, a platform
    tag on a pure package's wheel, is python's build's.
    """
    wheels = sorted(dist.glob("*.whl"))
    if not wheels:
        fail(f"{package.name}: the build produced no wheel in {dist}")
    pure = [wheel.name for wheel in wheels if "none-any" in wheel.name]
    if pure:
        fail(
            f"{package.name} holds nanobind and built a pure wheel:"
            f" {', '.join(pure)}. The extension module did not compile into"
            " the wheel; the platform tag is the proof it did."
        )


def host_is_musl() -> bool:
    """Whether this interpreter's C library is musl.

    Decides which linux wheel flavour the host can install: a
    musl-based interpreter (an Alpine runner's uv-managed python)
    takes the musllinux wheel, a glibc one the manylinux wheel. Read
    from the interpreter's build triple, with the loader the
    platform ships as the second signal, so a python built against
    musl on a glibc host still answers for itself.
    """
    triple = str(sysconfig.get_config_var("HOST_GNU_TYPE") or "")
    return "musl" in triple or _musl_loader_present()


def _musl_loader_present() -> bool:
    """Whether the platform ships musl's dynamic loader under ``/lib``."""
    return any(Path("/lib").glob("ld-musl-*.so.1"))


def build_wheels(
    package: Package,
    root: Path,
    dist: Path,
    *,
    epoch: int = 0,
    conan_install_args: str = "",
    one_host_wheel: bool = False,
) -> dict[str, str]:
    """Build this package's wheels into *dist*; the environment used.

    The wheel half of `build_dist`, split out so a leg can build a
    second set somewhere else without touching the collected
    ``dist/``. *conan_install_args* replaces what the package's own
    ``pyproject.toml`` passes to conan, which is how the floor leg adds
    the profile that pins the library it links. *one_host_wheel*
    builds one wheel for this machine alone, whatever set the matrix
    put in the environment: a proof about the library's C++ interface
    needs one interpreter, not the whole matrix.

    Raises:
        Failed: when cibuildwheel exits non-zero, or the wheels it
            wrote carry no platform tag.
    """
    env = dict(os.environ)
    if epoch:
        env["SOURCE_DATE_EPOCH"] = str(epoch)
    if conan_install_args:
        env["CONAN_INSTALL_ARGS"] = conan_install_args
    # One wheel for this machine: a linux run builds a manylinux and
    # a musllinux wheel of the same arch, and the host can install
    # only the one for its own libc, so the isolated leg would have
    # two candidates for one venv. The skip follows the host's libc;
    # the release matrix sets its own build set explicitly. One
    # architecture too, the machine's own: Windows would add a
    # 32-bit wheel beside the 64-bit one, and the isolated leg
    # installs the wheel it finds first.
    this_interpreter = f"cp{sys.version_info.major}{sys.version_info.minor}-*"
    this_libc = "*-manylinux_*" if host_is_musl() else "*-musllinux_*"
    if one_host_wheel:
        env["CIBW_BUILD"] = this_interpreter
        env["CIBW_SKIP"] = this_libc
        env["CIBW_ARCHS"] = "native"
    else:
        env.setdefault("CIBW_BUILD", this_interpreter)
        env.setdefault("CIBW_SKIP", this_libc)
        env.setdefault("CIBW_ARCHS", "native")
    for key, value in _container.conan_environment(root).items():
        env.setdefault(key, value)
    with _python.module_docs(package):
        result = tools.uv.opts(
            cwd=package.directory, env=env, nofail=True, recorded=False
        )(
            "tool",
            "run",
            "--from",
            CIBUILDWHEEL,
            "cibuildwheel",
            "--output-dir",
            str(dist),
        )
    if result.code != 0:
        # A native build's failure sits well above its end: a CMake
        # configure line, a compiler diagnostic, a linker's reason.
        fail(
            f"cibuildwheel ({package.name}) exited {result.code}:\n"
            f"{result.stdout[-20000:]}{result.stderr[-4000:]}"
        )
    assert_platform_tagged(package, dist)
    return env
