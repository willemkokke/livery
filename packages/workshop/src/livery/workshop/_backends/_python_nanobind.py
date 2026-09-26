"""The nanobind backend: build for ``type = "python-nanobind"``.

A child of the python backend: every quality verb is inherited
whole (the extension is a python distribution and every checker
applies), and only the wheel build differs. cibuildwheel builds
and repairs the wheel so even a single leg is
manylinux-compliant; the sdist still comes from ``uv build``.
"""

from __future__ import annotations

import os
import shutil
import sys
import sysconfig
from pathlib import Path
from typing import TYPE_CHECKING

import livery.footman as footman
from livery.footman import fail
from livery.toolroom import tools
from livery.workshop._backends import _python

if TYPE_CHECKING:
    from pathlib import Path

    from livery.workshop._packages import Package

check = _python.check
classify = _python.classify
gate_build = _python.gate_build
test = _python.test
current_version = _python.current_version
stamp_version = _python.stamp_version
publish_artifact = _python.publish_artifact


def declared_requirements(package: Package) -> dict[str, str]:
    """Both ecosystems' declarations: pyproject plus conanfile.

    The extension consumes python distributions at runtime and conan
    recipes at build time, so the lint judges its edges against the
    union. The contract names are disjoint homes for one dependency:
    an internal name appears in exactly one of the two.
    """
    from livery.workshop._backends import _cpp_conan

    return {
        **_python.declared_requirements(package),
        **_cpp_conan.declared_requirements(package),
    }


#: What ``uv tool run`` resolves for the build. A floor and a cap,
#: not an exact pin: the run has no lockfile to consult, and a major
#: bump changes cibuildwheel's defaults deliberately. The release
#: matrix (its own phase) pins its legs exactly.
CIBUILDWHEEL = "cibuildwheel>=3.0,<4"


def assert_platform_tagged(package: Package, dist: Path) -> None:
    """Refuse any pure-tagged wheel in *dist*; the identity guard.

    A wheel from a native kind that says ``none-any`` was built
    without its extension, and publishing it would hand every
    consumer an ImportError. The other half of the guard (a native
    tag from a pure kind) lives with the publish wave.
    """
    wheels = sorted(dist.glob("*.whl"))
    if not wheels:
        fail(f"{package.name}: the build produced no wheel in {dist}")
    pure = [wheel.name for wheel in wheels if "none-any" in wheel.name]
    if pure:
        fail(
            f"{package.name} is a python-nanobind package and built a"
            f" pure wheel: {', '.join(pure)}. The extension did not"
            " compile into the wheel; the platform tag is the proof it"
            " did."
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


def conan_environment(root: Path) -> dict[str, str]:
    """The variables a native build takes from the store; a caller's own win.

    `CMAKE_CONAN_PROVIDER` names the store's cmake-conan provider file;
    the extension's `pyproject.toml` maps it to CMake's
    `CMAKE_PROJECT_TOP_LEVEL_INCLUDES`, which CMake reads from no
    environment variable of its own, so every configure resolves
    `find_package` through conan. `CONAN_HOME` is made explicit so a
    container build shares this machine's cache and its editables. On
    Linux cibuildwheel
    builds in a container with its own filesystem, so the workspace,
    the store and the conan home are mounted at their host paths and
    the store's conan joins the container's PATH. The provider and
    conan are found through the entered environment first, then the
    store's own home.

    Raises:
        Failed: when the provider or conan is in neither place; the
            message names `fm sync`, which supplies both.
    """
    from livery.workshop._tools import store_home

    home = store_home()
    provider = os.environ.get("CMAKE_CONAN_PROVIDER") or _newest(
        home.tools, "cmake_conan", "conan_provider.cmake"
    )
    conan = shutil.which("conan") or _newest(home.tools, "conan", "bin/conan")
    if not provider or not conan:
        missing = "the cmake-conan provider" if not provider else "conan"
        fail(
            f"{missing} is not materialised: the native kinds take both from"
            f" the store; `{footman.prog()} sync` supplies them"
        )
    conan_home = os.environ.get("CONAN_HOME") or str(Path.home() / ".conan2")
    env = {"CMAKE_CONAN_PROVIDER": provider, "CONAN_HOME": conan_home}
    if sys.platform.startswith("linux"):
        mounts = " ".join(
            f"-v {path}:{path}" for path in (str(root), str(home.root), conan_home)
        )
        env["CIBW_ENVIRONMENT_PASS_LINUX"] = "CMAKE_CONAN_PROVIDER CONAN_HOME"
        env["CIBW_CONTAINER_ENGINE"] = f"docker; create_args: {mounts}"
        env["CIBW_ENVIRONMENT_LINUX"] = f'PATH="{Path(conan).parent}:$PATH"'
    return env


def _newest(tools_dir: Path, name: str, relative: str) -> str:
    """*relative* under the newest `<name>@<version>` in *tools_dir*, or empty."""
    from livery.toolroom.store import version_key

    found = [
        path for path in tools_dir.glob(f"{name}@*") if (path / relative).is_file()
    ]
    if not found:
        return ""
    newest = max(found, key=lambda p: version_key(p.name.split("@", 1)[1]))
    return str(newest / relative)


def build(package: Package, root: Path, *, epoch: int = 0) -> Path:
    """Build the platform wheel through cibuildwheel, and the sdist.

    Locally the run pins to the running interpreter
    (``CIBW_BUILD``, respected when the caller already set it), so
    the verb stays minutes; the release matrix widens the set in
    CI. Always from a clean ``dist/``, like the python backend.
    Refuses a pure-tagged result: see
    [livery.workshop._backends._python_nanobind.assert_platform_tagged][].
    """
    from livery.workshop._docs import materialise_module_docs

    materialise_module_docs(package)
    dist = package.directory / "dist"
    shutil.rmtree(dist, ignore_errors=True)
    env = dict(os.environ)
    if epoch:
        env["SOURCE_DATE_EPOCH"] = str(epoch)
    env.setdefault(
        "CIBW_BUILD",
        f"cp{sys.version_info.major}{sys.version_info.minor}-*",
    )
    # One wheel for this machine: a linux run builds a manylinux and
    # a musllinux wheel of the same arch, and the host can install
    # only the one for its own libc, so the isolated leg would have
    # two candidates for one venv. The skip follows the host's libc;
    # the release matrix sets its own build set explicitly.
    env.setdefault("CIBW_SKIP", "*-manylinux_*" if host_is_musl() else "*-musllinux_*")
    # And one architecture, the machine's own: Windows would add a
    # 32-bit wheel beside the 64-bit one, and the isolated leg installs
    # the wheel it finds first.
    env.setdefault("CIBW_ARCHS", "native")
    for key, value in conan_environment(root).items():
        env.setdefault(key, value)
    result = tools.uv.opts(cwd=package.directory, env=env, nofail=True, recorded=False)(
        "tool", "run", "--from", CIBUILDWHEEL, "cibuildwheel", "--output-dir", str(dist)
    )
    if result.code != 0:
        # A native build's failure sits well above its end: a CMake
        # configure line, a compiler diagnostic, a linker's reason.
        fail(
            f"cibuildwheel ({package.name}) exited {result.code}:\n"
            f"{result.stdout[-20000:]}{result.stderr[-4000:]}"
        )
    sdist = tools.uv.opts(cwd=package.directory, env=env, nofail=True, recorded=False)(
        "build", "--sdist", "--out-dir", str(dist)
    )
    if sdist.code != 0:
        fail(
            f"uv build --sdist ({package.name}) exited {sdist.code}:\n"
            f"{sdist.stdout[-4000:]}{sdist.stderr[-2000:]}"
        )
    assert_platform_tagged(package, dist)
    return dist
