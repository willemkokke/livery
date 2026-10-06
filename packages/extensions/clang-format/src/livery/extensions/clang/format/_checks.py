"""clang-format's check: each native package's C and C++ files, in its own style.

``format.clang-format`` judges one package at a time: the files its
claims reach in the package, or the files a run names
([livery.workshop.api.scoped_files][]). The style is the package's own
``.clang-format``, which the extension writes and a file deeper in the
tree may extend. The check hands clang-format the words after ``--``
on its own verb (``fm format.clang-format -- --verbose``). A body
resolves its runner on this module when it runs, so a test that
replaces `run_format` here sees its replacement called.
"""

from __future__ import annotations

import re
from pathlib import Path

import livery.toolroom.tools.api as tools
from livery.footman.api import fail, prog
from livery.workshop.api import (
    PACKAGE,
    CheckRecord,
    Claim,
    Fragment,
    GateContext,
    Package,
    scoped_files,
)

#: The kinds whose members carry C or C++.
KINDS = ("cpp-conan", "python-nanobind")

#: The suffixes clang-format reads.
SUFFIXES = (".cpp", ".cc", ".cxx", ".c", ".hpp", ".h", ".hxx")

#: The style each native package carries; a deeper file with
#: ``BasedOnStyle: InheritParentConfig`` adds that directory's own lines.
STYLE = """\
# Rendered by the template channel for the {{ kind }} kind; the gate keeps
# it matching its render. A file deeper in the tree with
# `BasedOnStyle: InheritParentConfig` carries this package's own lines.
BasedOnStyle: LLVM
IndentWidth: 4
ColumnLimit: 88
PointerAlignment: Left
"""

#: A violation line: the file, then its line and column, then the
#: complaint. The path is read up to the line number rather than to the
#: first colon, which on Windows is the drive letter.
_VIOLATION = re.compile(
    r"^(?P<path>.+?):\d+:\d+: (?:error|warning): code should be clang-formatted"
)


def unformatted(output: str) -> list[str]:
    """The files clang-format would rewrite, named once each, sorted."""
    found = {
        match["path"]
        for line in output.splitlines()
        if (match := _VIOLATION.match(line))
    }
    return sorted(found)


def run_format(
    package: Package,
    files: tuple[Path, ...],
    *,
    fix: bool,
    arguments: tuple[str, ...] = (),
) -> None:
    """Refuse a file of *package* clang-format would rewrite; *fix* rewrites it.

    The refusal names each file, because a person fixes files, not a
    diff. *arguments* go to clang-format after its mode and before the
    files.

    Raises:
        Failed: when a file is not formatted, or clang-format exits
            non-zero for a reason of its own.
    """
    if not files:
        return
    mode = ["-i"] if fix else ["--dry-run", "--Werror"]
    result = tools.clang_format.opts(
        cwd=package.directory, nofail=True, recorded=False
    )(*mode, *arguments, *(str(path) for path in files))
    if result.code == 0:
        return
    named = ", ".join(unformatted(result.stderr + result.stdout)) or "no file named"
    fail(
        f"{package.name}: clang-format would rewrite {named}."
        f" Run `{prog()} check --fix` to apply the package's own .clang-format."
    )


def _package(ctx: GateContext) -> Package:
    assert ctx.package is not None  # the gate hands a package check its package
    return ctx.package


def _format_run(ctx: GateContext) -> None:
    files = scoped_files(ctx, "format.clang-format")
    run_format(_package(ctx), files, fix=False, arguments=ctx.arguments)


def _format_fix(ctx: GateContext) -> None:
    files = scoped_files(ctx, "format.clang-format")
    run_format(_package(ctx), files, fix=True, arguments=ctx.arguments)


CHECKS = (
    CheckRecord(
        "clang-format",
        "format",
        _format_run,
        scope=PACKAGE,
        fix=_format_fix,
        kinds=KINDS,
        tools=("clang_format",),
        arguments=True,
        fragments=tuple(Fragment(".clang-format", STYLE, kind=kind) for kind in KINDS),
        claims=tuple(
            Claim(category, suffixes=SUFFIXES)
            for category in ("source", "test", "test-support")
        ),
    ),
)
