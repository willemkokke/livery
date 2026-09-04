"""The C/C++ backend: the build callables for ``type = "cpp-conan"``.

The gate verb configures and builds with cmake and ninja and runs
the ctest suite through the generated ``test`` target, all through
the toolroom handles. Packaging goes through conan, which has no
toolroom handle yet, so ``build`` starts it as a deliberate
``footman.run`` after probing that the binary resolves; a machine
without conan gets the install command, never a stack trace.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

import footman
import toolroom
from footman import fail

if TYPE_CHECKING:
    from livery.workshop._packages import Package

#: Where the gate's cmake configure lands, under the package.
#: conan's own cmake_layout also builds under build/, so one
#: gitignore entry covers both.
GATE_BUILD_DIR = "build/gate"


def conan_requirements(conanfile: Path) -> dict[str, str]:
    """The conan references a recipe declares, name to version text.

    Reads the ``requires`` class attribute (a string or a tuple) and
    ``self.requires("...")`` calls, without importing conan: the
    recipe is parsed as source. ``"acme-native/[>=0.1.0]"`` answers
    ``{"acme-native": "[>=0.1.0]"}``.
    """
    import ast

    refs: list[str] = []
    tree = ast.parse(conanfile.read_text("utf-8"))

    def _constants(node: ast.AST) -> list[str]:
        found = []
        for inner in ast.walk(node):
            if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                found.append(inner.value)
        return found

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "requires":
                    refs.extend(_constants(node.value))
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "requires"
        ):
            refs.extend(_constants(node.args[0]) if node.args else [])
    entries: dict[str, str] = {}
    for ref in refs:
        name, _, version = ref.partition("/")
        if name and version:
            entries[name] = version
    return entries


def declared_requirements(package: Package) -> dict[str, str]:
    """The conan references the package's recipe declares.

    The layering lint compares these against the contract's
    ``[[depends]]`` edges; a package without a conanfile declares
    nothing.
    """
    conanfile = package.directory / "conanfile.py"
    if not conanfile.is_file():
        return {}
    return conan_requirements(conanfile)


def current_version(package: Package) -> str:
    """The version the recipe's ``version`` attribute declares."""
    import re

    text = (package.directory / "conanfile.py").read_text("utf-8")
    match = re.search(r'^\s*version = "([^"]+)"$', text, re.M)
    return match.group(1) if match else "0.0.0"


def stamp_version(package: Package) -> _Stamper:
    """Where a conan package's version lives, ready to stamp."""
    return _Stamper(package)


class _Stamper:
    """Stamp a version into the recipe's ``version``, idempotently."""

    def __init__(self, package: Package) -> None:
        self._package = package

    def stamp(self, version: str) -> list[str]:
        """Write *version* into ``conanfile.py``; what changed."""
        import re

        conanfile = self._package.directory / "conanfile.py"
        text = conanfile.read_text("utf-8")
        stamped, count = re.subn(
            r'^(\s*)version = "[^"]+"$',
            rf'\g<1>version = "{version}"',
            text,
            count=1,
            flags=re.M,
        )
        if count != 1:
            fail(f"{conanfile} has no version line to stamp")
        if stamped == text:
            return []
        conanfile.write_text(stamped, encoding="utf-8")
        return ["conanfile.py"]


def _conan(
    package_dir: Path, *args: str, env: dict[str, str] | None = None
) -> footman.Result:
    """One conan invocation; the refusal teaches the install."""
    if shutil.which("conan") is None:
        fail(
            "conan is not on PATH: install it (uv tool install conan,"
            " or pip install conan) and re-run"
        )
    run_env = dict(os.environ)
    if env:
        run_env.update(env)
    return footman.run(
        ["conan", *args],
        cwd=package_dir,
        env=run_env,
        nofail=True,
        recorded=False,
    )


#: The remote name the workshop configures on the conan client. One
#: name, always re-pointed at the resolved target, so a stale remote
#: from an earlier workspace cannot swallow an upload.
CONAN_REMOTE = "workshop"


def publish(package: Package, target_url: str, *, version: str, local: bool) -> bool:
    """Upload the recipe to the resolved target; False when already there.

    A remote target gets ``conan upload`` through the ``workshop``
    remote (re-pointed at *target_url* first, so the name never
    drifts). A folder target gets ``conan cache save``: the saved
    tarball lands as ``<dir>/<name>-<version>.tgz``, and
    ``conan cache restore`` reads it back on any machine.
    Credentials stay conan-native: ``CONAN_LOGIN_USERNAME`` and
    ``CONAN_PASSWORD``, taught by the refusal when the remote wants
    them.
    """
    ref = f"{package.name}/{version}"
    if local:
        directory = Path(target_url)
        directory.mkdir(parents=True, exist_ok=True)
        archive = directory / f"{package.name}-{version}.tgz"
        result = _conan(
            package.directory,
            "cache",
            "save",
            f"{ref}:*",
            "--file",
            str(archive),
        )
        if result.code != 0:
            fail(
                f"conan cache save ({ref}) exited {result.code}:\n"
                f"{result.stdout[-3000:]}{result.stderr[-2000:]}"
            )
        return True
    added = _conan(
        package.directory,
        "remote",
        "add",
        CONAN_REMOTE,
        target_url,
        "--force",
    )
    if added.code != 0:
        fail(
            f"conan remote add {CONAN_REMOTE} {target_url} exited"
            f" {added.code}:\n{added.stdout}{added.stderr}"
        )
    result = _conan(package.directory, "upload", ref, "-r", CONAN_REMOTE, "--confirm")
    if result.code != 0:
        output = f"{result.stdout}{result.stderr}"
        if "already" in output.lower() and "exist" in output.lower():
            print(f"  {package.name}: already uploaded; walking past")
            return False
        hint = ""
        if "401" in output or "auth" in output.lower():
            hint = (
                "\n  the remote wants credentials: set"
                " CONAN_LOGIN_USERNAME and CONAN_PASSWORD (conan's own"
                " variables) and re-run"
            )
        fail(f"conan upload ({ref}) exited {result.code}:\n{output[-3000:]}{hint}")
    return True


class ConanRegistry:
    """The wave's probe against a conan target; fits the Registry seam.

    A remote target answers from ``conan list`` against the
    ``workshop`` remote; a folder target answers from the saved
    tarball names in the directory.
    """

    def __init__(self, target_url: str, *, local: bool, cwd: Path) -> None:
        self._url = target_url
        self._local = local
        self._cwd = cwd

    def versions(self, name: str) -> tuple[str, ...]:
        """The published versions of *name* at the target."""
        if self._local:
            directory = Path(self._url)
            prefix = f"{name}-"
            return tuple(
                sorted(
                    path.name[len(prefix) : -len(".tgz")]
                    for path in directory.glob(f"{prefix}*.tgz")
                )
            )
        import json

        _conan(self._cwd, "remote", "add", CONAN_REMOTE, self._url, "--force")
        result = _conan(
            self._cwd,
            "list",
            f"{name}/*",
            "-r",
            CONAN_REMOTE,
            "--format=json",
        )
        if result.code != 0:
            return ()
        try:
            data = json.loads(result.stdout)
        except ValueError:
            return ()
        listed = data.get(CONAN_REMOTE, {})
        versions = []
        for ref in listed:
            _, _, version = str(ref).partition("/")
            if version:
                versions.append(version)
        return tuple(sorted(versions))


def check(package: Package, root: Path) -> None:
    """Configure, build, and ctest *package*; a refusal is the verdict.

    The dependency-free library needs no conan at gate time: cmake
    configures against the host toolchain, ninja builds, and the
    generated ``test`` target runs ctest. A package that declares
    conan requirements gains a conan install step when the
    cross-kind dependency lands; today a missing generator file
    fails the configure with cmake's own message.
    """
    del root
    build_dir = package.directory / GATE_BUILD_DIR
    cmake = toolroom.cmake.opts(cwd=package.directory)
    cmake("-S", ".", "-B", str(build_dir), "-G", "Ninja")
    cmake("--build", str(build_dir))
    # The Ninja generator's `test` target runs ctest with the
    # verdict in the exit code; CTEST_OUTPUT_ON_FAILURE makes a red
    # test print its output instead of a bare summary line. The env
    # rides whole: standalone toolroom passes `env=` as the child's
    # entire environment, never a merge over the parent's.
    result = toolroom.cmake.opts(
        cwd=package.directory,
        env={**os.environ, "CTEST_OUTPUT_ON_FAILURE": "1"},
        nofail=True,
    )("--build", str(build_dir), "--target", "test")
    if result.code != 0:
        fail(
            f"{package.name}: ctest failed (exit {result.code}):\n"
            f"{result.stdout[-4000:]}{result.stderr[-2000:]}"
        )


def build(package: Package, root: Path, *, epoch: int = 0) -> Path:
    """Package *package* through ``conan create``; the conan cache dir.

    ``conan create`` exports the recipe, builds in the cache, and
    packages the result there; publishing uploads from the cache, so
    nothing lands in a ``dist/`` directory. Returns the package's
    ``build`` directory as the artifact location the caller can
    inspect. *epoch* is accepted for the backend contract; conan
    stamps its own metadata and the reproducibility guard for this
    kind is a later phase's work.
    """
    del root, epoch
    if shutil.which("conan") is None:
        fail(
            f"{package.name} is a cpp-conan package and conan is not on"
            " PATH: install it (uv tool install conan, or pip install"
            " conan) and re-run"
        )
    conan = footman.run(
        ["conan", "profile", "detect", "--exist-ok"],
        cwd=package.directory,
        nofail=True,
        recorded=False,
    )
    if conan.code != 0:
        fail(f"conan profile detect exited {conan.code}:\n{conan.stdout}{conan.stderr}")
    result = footman.run(
        ["conan", "create", "."],
        cwd=package.directory,
        nofail=True,
        recorded=False,
    )
    if result.code != 0:
        fail(
            f"conan create ({package.name}) exited {result.code}:\n"
            f"{result.stdout[-4000:]}{result.stderr[-2000:]}"
        )
    return package.directory / "build"
