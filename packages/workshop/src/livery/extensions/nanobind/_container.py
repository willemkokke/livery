"""The conan environment a nanobind build takes, and the Linux build container's.

Every build of a nanobind package resolves its C++ dependencies through
the cmake-conan provider, so it takes the provider's file and conan from
the store (`conan_environment`). On Linux cibuildwheel builds in a
container with its own filesystem: this module also says what the
container mounts, the conan it installs for itself, and the conan
workspace file it reads.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import livery.footman as footman
from livery.footman import fail

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from livery.workshop._tools import Receipt


def conan_environment(root: Path) -> dict[str, str]:
    """The variables a native build takes from the store; a caller's own win.

    `CMAKE_CONAN_PROVIDER` names the store's cmake-conan provider file;
    the package's `pyproject.toml` maps it to CMake's
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
    wheel builds (`container_conan_install`) and the store's one stays
    off its PATH. The container runs as root, and the conan it runs
    gives what it writes in the home back to the home's owner after
    every call (`container_conan_wrapper`). The package itself is
    copied to ``/project`` there, outside the workspace, so the
    container also mounts a workspace file of its own beside it
    (`container_workspace`), and a sibling resolves from its source in
    the container too. The provider and conan are read from the
    entered environment first, then from this checkout's receipts,
    which name the versions its lock pins.

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
    (`container_conan_wrapper`); the store's conan, the host's frozen
    binary, is never run there.
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
    from livery.extensions.conan import _package as _conan

    if not (root / _conan.WORKSPACE_FILE).is_file():
        return None
    path = root / ".workshop" / ".cache" / "conan" / _conan.WORKSPACE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_conan.workspace_file(root, absolute=True), encoding="utf-8")
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
        f"{' and '.join(missing)} is not here: a nanobind build takes both"
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
