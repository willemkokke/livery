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
from typing import TYPE_CHECKING

import footman
import toolroom
from footman import fail

if TYPE_CHECKING:
    from pathlib import Path

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
