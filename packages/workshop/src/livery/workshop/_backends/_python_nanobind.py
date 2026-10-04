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

import livery.footman.api as footman
import livery.toolroom.tools.api as tools
from livery.footman.api import fail
from livery.workshop._backends import _python

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from livery.workshop._packages import Package
    from livery.workshop._tools import Receipt

gate_build = _python.gate_build
test = _python.test
current_version = _python.current_version
stamp_version = _python.stamp_version
publish_artifact = _python.publish_artifact
# The python half is read exactly as a pure package's is; the
# native half references its conan dependency through the recipe,
# which the conan kind answers for.
module_roots = _python.module_roots
referenced_siblings = _python.referenced_siblings


def declare_requirement(package: Package, dependency: Package, floor: str) -> list[str]:
    """Write the requirement where the dependency's kind lives.

    The pyproject for a python sibling, the recipe for a conan one.
    """
    from livery.workshop._backends import _cpp_conan
    from livery.workshop._kinds import is_python_kind

    if is_python_kind(dependency.kind):
        return _python.declare_requirement(package, dependency, floor)
    return _cpp_conan.declare_requirement(package, dependency, floor)


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


#: What a package passes to conan install when its own pyproject
#: names nothing: build what the cache lacks, leave the rest alone.
DEFAULT_INSTALL_ARGS = "--build=missing"


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


def floor_legs(
    package: Package, root: Path, released: dict[str, str], *, epoch: int = 0
) -> None:
    """Prove every native floor this extension declares, one leg each.

    A floor is a claim: the extension compiles and its tests pass
    against that version of the library, so a consumer who resolves
    the floor gets a working extension. The claim is proved here.
    Per conan dependency: the floor's saved cache is restored from
    its own release, one wheel is built for this machine with the
    requirement replaced by the floor, and the package's tests run
    against that wheel in a fresh venv. The pin is a conan profile
    holding a `[replace_requires]` line, added to the ones the
    dependency provider already passes, so the range the recipe
    declares resolves to that version alone.

    A floor equal to the version this wave releases is the package
    the main build already linked, so that leg says so and does
    nothing. *released* is the wave's manifest, member path to
    version.

    Raises:
        Failed: when the floor's cache is not on its release, when
            the pinned build fails, or when the tests fail against
            it. A floor whose header lacks a symbol the extension
            calls fails at the compile.
    """
    from livery.workshop._backends import _cpp_conan, _python
    from livery.workshop._kinds import kind_for
    from livery.workshop._packages import discover_packages

    by_path = {member.path: member for member in discover_packages(root)}
    for edge in package.depends:
        dependency = by_path.get(edge.path)
        floor = getattr(edge, "floor", "")
        if dependency is None or not floor:
            continue
        if kind_for(dependency.kind).artifact != "conan":
            continue
        if released.get(edge.path) == floor:
            print(
                f"  {package.name}: the floor on {dependency.name} is"
                f" {floor}, the version this wave releases; the build"
                " above already linked it"
            )
            continue
        _cpp_conan.restore_from_releases(
            root, dependency.name, floor, cwd=package.directory
        )
        dist = package.directory / "build" / "floor"
        shutil.rmtree(dist, ignore_errors=True)
        profile = package.directory / "build" / "floor-profile.txt"
        profile.parent.mkdir(parents=True, exist_ok=True)
        profile.write_text(
            f"[replace_requires]\n{dependency.name}/*: {dependency.name}/{floor}\n",
            encoding="utf-8",
        )
        # A posix path: the string rides a CMake list, where a
        # Windows separator would read as an escape.
        pinned = f"{conan_install_args(package)};-pr:h;{profile.as_posix()}"
        build_wheels(
            package,
            root,
            dist,
            epoch=epoch,
            conan_install_args=pinned,
            one_host_wheel=True,
        )
        _python.run_isolated_test(package, root, wheels_dir=dist)
        print(f"  {package.name}: floor leg green against {dependency.name} {floor}")


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
    the store's conan joins the container's PATH. Both are read from
    the entered environment first, then from this checkout's
    receipts, which name the versions its lock pins.

    Raises:
        Failed: when the provider or conan is in neither place; the
            message names `fm sync`, which supplies both.
    """
    from livery.workshop._tools import store_home

    home = store_home()
    provider, conan, gap = conan_tools(root)
    if gap:
        fail(gap)
    conan_home = os.environ.get("CONAN_HOME") or str(Path.home() / ".conan2")
    env = {"CMAKE_CONAN_PROVIDER": provider, "CONAN_HOME": conan_home}
    if sys.platform.startswith("linux"):
        mounts = container_mounts(
            (root, home.root, Path(conan_home), Path(provider).parent, Path(conan))
        )
        env["CIBW_ENVIRONMENT_PASS_LINUX"] = (
            "CMAKE_CONAN_PROVIDER CONAN_HOME CONAN_INSTALL_ARGS"
        )
        env["CIBW_CONTAINER_ENGINE"] = f"docker; create_args: {mounts}"
        env["CIBW_ENVIRONMENT_LINUX"] = f'PATH="{conan}:$PATH"'
    return env


def container_mounts(paths: Sequence[Path]) -> str:
    """The docker arguments that mount each of *paths* at its own path.

    A path inside one already mounted adds nothing, so it is left out.
    The provider and conan are mounted beside the store because the
    entered environment can name another store than this process's
    (a test's isolated store under a CI leg's), and the container sees
    only what is mounted.
    """
    kept: list[Path] = []
    for path in paths:
        if not any(path == held or path.is_relative_to(held) for held in kept):
            kept = [held for held in kept if not held.is_relative_to(path)]
            kept.append(path)
    return " ".join(f"-v {path}:{path}" for path in kept)


def _receipted_env(held: Mapping[str, Receipt], name: str, variable: str) -> str:
    """The value *name*'s receipt records for *variable*, or empty.

    A tool that sets a variable declares it in its record, so this
    reads the record's answer through the receipt rather than
    rebuilding the path from a file name.
    """
    receipt = held.get(name)
    return receipt.env.get(variable, "") if receipt is not None else ""


def conan_tools(root: Path) -> tuple[str, str, str]:
    """The provider file, conan's directory, and why either is missing.

    The provider comes from the environment first, so a caller who
    names one wins; its value there is written from the record anyway.
    Conan comes from *root*'s receipt first, which names the version
    the lock pins, and from PATH second: a conan on PATH that is not
    the store's is an accident rather than a decision, and a pinned
    tool that another install can shadow is not pinned.

    PATH is what makes a workspace with no receipts of its own work. A
    CI leg and an entered shell both carry the store's tools there,
    because the entry puts every receipt's paths on PATH, so a caller
    running against a temporary directory still resolves the store's
    conan.

    Returns:
        The provider file, the directory conan runs from, and a reason
        when one of them is missing. The reason is empty when both are
        there, and names only what is missing.
    """
    from livery.workshop._tools import receipts

    held = receipts(root)
    # The provider's path is the cmake-conan record's own env entry,
    # so nothing here spells the file name: a record that moves it
    # moves this too.
    provider = os.environ.get("CMAKE_CONAN_PROVIDER") or _receipted_env(
        held, "cmake_conan", "CMAKE_CONAN_PROVIDER"
    )
    conan = _receipted_path(held, "conan") or _resolved_dir("conan")
    missing = [
        name
        for name, found in (("the cmake-conan provider", provider), ("conan", conan))
        if not found
    ]
    if not missing:
        return provider, conan, ""
    return (
        provider,
        conan,
        f"{' and '.join(missing)} is not here: the native kinds take both"
        f" from the store; `{footman.prog()} sync` supplies them, and an"
        " entered environment carries them",
    )


def _resolved_dir(name: str) -> str:
    """The directory *name* runs from in this environment, or empty."""
    found = shutil.which(name)
    return str(Path(found).parent) if found else ""


def _receipted_path(held: Mapping[str, Receipt], name: str) -> str:
    """The directory *name*'s receipt puts on PATH, or empty.

    Empty for a tool this checkout never materialised, and for one
    materialised in a mode that puts no directory on PATH.
    """
    receipt = held.get(name)
    return receipt.paths[0] if receipt is not None and receipt.paths else ""


def build(package: Package, root: Path, *, epoch: int = 0) -> Path:
    """Build the platform wheel through cibuildwheel, and the sdist.

    Locally the run pins to the running interpreter
    (``CIBW_BUILD``, respected when the caller already set it), so
    the verb stays minutes; the release matrix widens the set in
    CI. Always from a clean ``dist/``, like the python backend.
    Refuses a pure-tagged result: see
    [livery.workshop._backends._python_nanobind.assert_platform_tagged][].
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

    The wheel half of
    [livery.workshop._backends._python_nanobind.build][], split out
    so a leg can build a second set somewhere else without touching
    the collected ``dist/``. *conan_install_args* replaces what the
    package's own ``pyproject.toml`` passes to conan, which is how
    the floor leg adds the profile that pins the library it links.
    *one_host_wheel* builds
    one wheel for this machine alone, whatever set the matrix put in
    the environment: a proof about the library's C++ interface needs
    one interpreter, not the whole matrix.

    Raises:
        Failed: when cibuildwheel exits non-zero, or the wheels it
            wrote carry no platform tag.
    """
    from livery.workshop._docs_contract import materialise_module_docs

    materialise_module_docs(package)
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
    assert_platform_tagged(package, dist)
    return env
