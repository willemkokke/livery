"""The nanobind backend: build for ``type = "python-nanobind"``.

A child of the python backend: every quality verb is inherited
whole (the extension is a python distribution and every checker
applies), and only the wheel build differs. cibuildwheel builds
and repairs the wheel so even a single leg is
manylinux-compliant; the sdist still comes from ``uv build``. The
release's build on one host, which the wheels job's legs run, is
here too ([livery.workshop._backends._python_nanobind.host_build][]).
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import sys
import sysconfig
from pathlib import Path
from typing import TYPE_CHECKING

import livery.footman as footman
import livery.toolroom.tools as tools
from livery.extensions.python import _backend as _python
from livery.footman import fail

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from livery.workshop._packages import Package
    from livery.workshop._tools import Receipt

gate_build = _python.gate_build
test = _python.test
current_version = _python.current_version
stamp_version = _python.stamp_version
publish_artifact = _python.publish_artifact
isolated_test = _python.isolated_test
verify = _python.verify
# The python half is read exactly as a pure package's is; the
# native half references its conan dependency through the recipe,
# which the conan kind answers for.
module_roots = _python.module_roots
public_modules = _python.public_modules
compile_commands = _python.compile_commands
references = _python.references

#: The extensions a ``python-nanobind`` package that lists none is taken
#: to hold beside python's: cpp for its extension module's sources,
#: conan for the provider that resolves their dependencies, which it
#: consumes without being a recipe, and nanobind for the release's
#: wheels job. The sync step and the writers of a requirement on a
#: sibling apply through it; the lifecycle phases read the listed
#: extensions alone.
STANDS_FOR = ("cpp", "conan", "nanobind")


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
    from livery.extensions.python import _backend as _python
    from livery.workshop._backends import _cpp_conan
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


def host_build(root: Path) -> None:
    """Build this host's native artifacts for the release at *root*'s HEAD.

    Each leg of the release's wheels job runs this before the wave,
    over the members of the release that the workspace's
    ``job-built-members`` answer names, in three steps. Each of them
    that releases a conan package is created into this leg's cache and
    saved beside its wheels, one file per host, which is how a forge
    with no conan registry carries the package. Then cibuildwheel
    builds every other one, a platform-wheel member, for the
    interpreters the caller's ``CIBW_BUILD`` names, resolving each
    conan member from the cache the first step filled. Then every
    declared floor on a conan member is proved with its own build. The
    publish job collects each leg's ``dist/``, and the wave publishes
    the union. A release with no such member prints so and builds
    nothing, so the leg stays green on a pure release.
    """
    from livery import git
    from livery.workshop import _lifecycle
    from livery.workshop._backends import _cpp_conan
    from livery.workshop._kinds import kind_for
    from livery.workshop._publish import discover_release
    from livery.workshop._queries import JOB_BUILT_MEMBERS, answer

    resolved_ref = git.head_sha(root)
    epoch = int(git.run(root, "log", "-1", "--format=%ct", resolved_ref).strip() or "0")
    members = discover_release(root, resolved_ref)
    released = {package.path: version for package, version in members}
    built = answer(root, JOB_BUILT_MEMBERS)
    conan_members: list[tuple[Package, str]] = []
    native: list[Package] = []
    # The kind's artifact says which of them is a conan package, until
    # the kinds go.
    for package, version in members:
        if package.path not in built:
            continue
        if kind_for(package.kind).artifact == "conan":
            conan_members.append((package, version))
        else:
            native.append(package)
    if not native and not conan_members:
        print("  no native members in this release; nothing to build")
        return
    # A release leg builds the packages the release ships, so a sibling
    # resolves to the package this leg created, never to its source
    # tree: the conan workspace is set aside for every build.
    with _cpp_conan.workspace_aside(root):
        for package, version in conan_members:
            _lifecycle.build(package, root, epoch=epoch)
            dist = package.directory / "dist"
            archive = _cpp_conan.save_cache(package, version, dist)
            print(f"  {package.name}: {archive.name}")
        for package in native:
            dist = _lifecycle.build(package, root, epoch=epoch)
            wheels = ", ".join(sorted(w.name for w in dist.glob("*.whl")))
            print(f"  {package.name}: {wheels}")
        for package in native:
            floor_legs(package, root, released, epoch=epoch)


def assert_platform_tagged(package: Package, dist: Path) -> None:
    """Refuse any pure-tagged wheel in *dist*; the identity guard.

    A wheel from a native kind that says ``none-any`` was built
    without its extension, and publishing it would hand every
    consumer an ImportError. The other half of the guard, a platform
    tag on a pure package's wheel, is python's build's.
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
    container build shares this machine's cache, and the home is
    created here when it is missing: a mount of a missing directory
    would create it owned by root. On Linux cibuildwheel builds in a
    container with its own filesystem, so the workspace, the store, the
    provider and the conan home are mounted at their host paths. The
    store's conan is the host's frozen binary, linked against the
    host's libc, which a manylinux or musllinux container cannot run,
    so the container installs conan at the same version before any
    wheel builds
    ([livery.workshop._backends._python_nanobind.container_conan_install][])
    and the store's one stays off its PATH. The container runs as root,
    and the conan it runs gives what it writes in the home back to the
    home's owner after every call
    ([livery.workshop._backends._python_nanobind.container_conan_wrapper][]).
    The package itself is
    copied to ``/project`` there, outside the workspace, so the
    container also mounts a workspace file of its own beside it
    ([livery.workshop._backends._python_nanobind.container_workspace][]),
    and a sibling resolves from its source in the container too. The
    provider and conan are read from the entered environment first,
    then from this checkout's receipts, which name the versions its
    lock pins.

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
    Path(conan_home).mkdir(parents=True, exist_ok=True)
    env = {"CMAKE_CONAN_PROVIDER": provider, "CONAN_HOME": conan_home}
    if sys.platform.startswith("linux"):
        mounts = container_mounts(
            (root, home.root, Path(conan_home), Path(provider).parent)
        )
        workspace = container_workspace(root)
        if workspace is not None:
            mounts += f" -v {workspace}:{CONTAINER_WORKSPACE}:ro"
        env["CIBW_ENVIRONMENT_PASS_LINUX"] = (
            "CMAKE_CONAN_PROVIDER CONAN_HOME CONAN_INSTALL_ARGS"
        )
        env["CIBW_CONTAINER_ENGINE"] = f"docker; create_args: {mounts}"
        env["CIBW_BEFORE_ALL_LINUX"] = container_conan_install(
            conan_version(root, conan)
        )
    return env


def conan_version(root: Path, conan: str) -> str:
    """The version of the conan a build uses: the receipt's, else the binary's own.

    The receipt names the version the lock pins. A workspace with no
    receipts of its own (a CI leg's test building in a temporary
    directory) resolved conan from PATH, and that binary's
    ``--version`` is the one answer left; the cmake-conan provider
    reads it the same way.

    Raises:
        Failed: when neither a receipt nor the binary names a version.
    """
    from livery.workshop._tools import receipts

    held = receipts(root).get("conan")
    if held is not None and held.version:
        return held.version
    why = "a Linux build container installs conan at the host's version"
    remedy = f"`{footman.prog()} sync` writes the receipt that names it"
    try:
        result = footman.run([str(Path(conan) / "conan"), "--version"], nofail=True)
    except FileNotFoundError:
        fail(f"no conan at {conan}, and {why}; {remedy}")
    found = re.search(r"(\d+\.\d+\.\d+)", f"{result.stdout}{result.stderr}")
    if result.code != 0 or found is None:
        fail(
            f"conan at {conan} names no version (`conan --version` exited"
            f" {result.code}), and {why}; {remedy}"
        )
    return found.group(1)


#: Where a Linux build container keeps the conan it installs for itself.
CONTAINER_CONAN = "/opt/workshop-conan"

#: What a Linux build container runs as ``conan``: a script on every
#: build's PATH, in front of the container's own install.
CONTAINER_CONAN_COMMAND = "/usr/local/bin/conan"


def container_conan_install(version: str) -> str:
    """The shell line a Linux build container runs before any wheel builds.

    A virtual environment of the container's own python, conan at
    *version* installed into it from the index, and the script that
    runs it written to `CONTAINER_CONAN_COMMAND`
    ([livery.workshop._backends._python_nanobind.container_conan_wrapper][]);
    the store's conan, the host's frozen binary, is never run there.
    """
    return (
        f"python -m venv {CONTAINER_CONAN}"
        f" && {CONTAINER_CONAN}/bin/pip install --quiet conan=={version}"
        f" && {write_script(CONTAINER_CONAN_COMMAND, container_conan_wrapper())}"
    )


def container_conan_wrapper(conan: str = f"{CONTAINER_CONAN}/bin/conan") -> str:
    """The script a Linux build container runs as ``conan``: *conan*, then a chown.

    The container runs as root and mounts the host's conan home, so a
    file its conan writes there belongs to root on the host, where the
    host's user cannot remove it: the host's conan then fails to replace
    that package's folder, and a removal of the home stops at it. After
    every call the script gives the whole home to the user who owns the
    home's directory, as the container sees it, which is the host's user
    under docker; under rootless docker it is the container's own root,
    and nothing changes. The script exits with *conan*'s code. A build
    killed during a call leaves that call's files with root.
    """
    return "\n".join(
        (
            "#!/bin/sh",
            f'{conan} "$@"',
            "code=$?",
            'if [ -d "$CONAN_HOME" ]; then',
            '  chown -R "$(stat -c %u:%g "$CONAN_HOME")" "$CONAN_HOME"',
            "fi",
            'exit "$code"',
            "",
        )
    )


def write_script(path: str, text: str) -> str:
    """The shell line that writes *text* to *path* as an executable script.

    Each line of *text* is one single-quoted argument to ``printf``, so
    the shell that runs the line expands nothing in it. Whatever is at
    *path* goes first, so the line never writes through a link.
    """
    lines = " ".join(shlex.quote(line) for line in text.splitlines())
    target = shlex.quote(path)
    return f"rm -f {target} && printf '%s\\n' {lines} > {target} && chmod 755 {target}"


#: Where a Linux build container finds the conan workspace file: beside
#: the package, which cibuildwheel copies to ``/project`` and builds
#: below it. Conan looks for the file from the directory a command runs
#: in up to, but not including, ``/``, so ``/project`` is the one place
#: above the build directory that it reaches. cibuildwheel copies into
#: ``/project`` without clearing it, so the mounted file stays.
CONTAINER_WORKSPACE = "/project/conanws.yml"


def container_workspace(root: Path) -> Path | None:
    """The conan workspace file a Linux build container mounts; None without one.

    cibuildwheel copies the package to ``/project`` in its container,
    and conan looks for the workspace file in the directory a command
    runs in and that directory's parents, so the root's own file is
    never found from there. The container mounts the root at its host
    path, so a copy that names each member by its absolute path
    resolves each member from its source; it is written under the
    root's ``.workshop/.cache/conan/`` and mounted at
    `CONTAINER_WORKSPACE`. None when the root holds no workspace file,
    as in a release leg that set it aside so a sibling resolves from
    the package the leg created.
    """
    from livery.workshop._backends import _cpp_conan

    if not (root / _cpp_conan.WORKSPACE_FILE).is_file():
        return None
    path = root / ".workshop" / ".cache" / "conan" / _cpp_conan.WORKSPACE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_cpp_conan.workspace_file(root, absolute=True), encoding="utf-8")
    return path


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
