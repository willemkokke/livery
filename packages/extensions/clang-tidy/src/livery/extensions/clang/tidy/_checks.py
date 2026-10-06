"""clang-tidy's check: each cpp package's sources, over its compilation database.

``lint.clang-tidy`` judges one package at a time, after the package's
build is configured: the files its claims reach in the package, or the
files a run names ([livery.workshop.api.scoped_files][]), against the
compilation database the package's kind says its build writes
([livery.workshop.api.compile_commands][]). The checks are the
package's own ``.clang-tidy``, which the extension writes. The check
hands clang-tidy the words after ``--`` on its own verb
(``fm lint.clang-tidy -- --checks=-*,bugprone-*``). A body resolves its
runner on this module when it runs, so a test that replaces `run_lint`
here sees its replacement called.
"""

from __future__ import annotations

import sys
from pathlib import Path

import livery.footman.api as footman
import livery.toolroom.tools.api as tools
from livery.footman.api import fail
from livery.workshop.api import (
    PACKAGE,
    CheckRecord,
    Claim,
    Fragment,
    GateContext,
    Package,
    compile_commands,
    scoped_files,
)

#: The kind whose members build with a compilation database to read.
KINDS = ("cpp-conan",)

#: The kinds whose members carry the checks' file, the native ones.
CARRIERS = ("cpp-conan", "python-nanobind")

#: The suffixes clang-tidy reads.
SUFFIXES = (".cpp", ".cc", ".cxx", ".c", ".hpp", ".h", ".hxx")

#: The checks each native package carries; a deeper file with
#: ``InheritParentConfig: true`` adds that directory's own lines.
CHECKS_FILE = """\
# Rendered by the template channel for the {{ kind }} kind; the gate keeps
# it matching its render. A `.clang-tidy` deeper in the tree with
# `InheritParentConfig: true` carries this package's own lines.
#
# The families a gate can hold green from the first commit: the bug
# and portability checks, and the performance ones. readability-* is
# left out on purpose, since its opinions collide with clang-format's
# and with each other. A finding is an error, so the gate's verdict
# stays its exit code.
Checks: >
  bugprone-*,
  performance-*,
  portability-*,
  -bugprone-easily-swappable-parameters
WarningsAsErrors: "*"
HeaderFilterRegex: "^$"
"""


def _asked(argv: list[str]) -> str:
    """The first line *argv* prints, or empty when it will not run."""
    try:
        answer = footman.run(argv, nofail=True, recorded=False)
    except OSError:
        return ""
    lines = (answer.stdout or "").strip().splitlines()
    return lines[0] if answer.code == 0 and lines else ""


def toolchain_arguments() -> tuple[list[str], str]:
    """What the standalone clang-tidy needs, and why it cannot run.

    The static build is one binary. It carries no resource directory of
    its own, so the compiler's builtin headers (`stddef.h` and its kin)
    come from the host's compiler, and on macOS the standard library
    comes from the SDK xcrun names. Returns the arguments and an empty
    reason, or no arguments and the reason the lint cannot run, which
    the caller prints as a skip.
    """
    resources = _asked(["clang", "-print-resource-dir"]) or _asked(
        ["cc", "-print-file-name=include"]
    )
    if not resources:
        return [], "no compiler here answers where its builtin headers are"
    arguments = [f"--extra-arg=-resource-dir={resources.removesuffix('/include')}"]
    if sys.platform == "darwin":
        sdk = _asked(["xcrun", "--show-sdk-path"])
        if not sdk:
            return [], "xcrun names no SDK, where this platform keeps its headers"
        arguments.append(f"--extra-arg=-isysroot{sdk}")
    return arguments, ""


def run_lint(
    package: Package,
    files: tuple[Path, ...],
    database: Path,
    arguments: tuple[str, ...] = (),
) -> None:
    """Run clang-tidy over *files* of *package* against *database*; a finding refuses.

    A host that cannot give the standalone binary its headers skips,
    saying why. *arguments* go to clang-tidy before the files.

    Raises:
        Failed: when clang-tidy finds anything, with its own output.
    """
    if not files:
        return
    toolchain, reason = toolchain_arguments()
    if reason:
        print(f"  {package.name}: clang-tidy skips, {reason}")
        return
    result = tools.clang_tidy.opts(cwd=package.directory, nofail=True, recorded=False)(
        "-p",
        str(database.parent),
        *toolchain,
        *arguments,
        *(str(path) for path in files),
    )
    if result.code != 0:
        fail(
            f"{package.name}: clang-tidy found something:\n"
            f"{result.stdout[-4000:]}{result.stderr[-2000:]}"
        )


def _lint_run(ctx: GateContext) -> None:
    package = ctx.package
    assert package is not None  # the gate hands a package check its package
    database = compile_commands(package)
    if database is None or not database.is_file():
        return
    run_lint(package, scoped_files(ctx, "lint.clang-tidy"), database, ctx.arguments)


CHECKS = (
    CheckRecord(
        "clang-tidy",
        "lint",
        _lint_run,
        scope=PACKAGE,
        kinds=KINDS,
        after=("build.configure",),
        tools=("clang_tidy",),
        arguments=True,
        fragments=tuple(
            Fragment(".clang-tidy", CHECKS_FILE, kind=kind) for kind in CARRIERS
        ),
        claims=(Claim("source", suffixes=SUFFIXES), Claim("test", suffixes=SUFFIXES)),
    ),
)
