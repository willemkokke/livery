"""Ruff's two checks: the formatter, then the linter, over the python files they claim.

Each check narrows by paths: the workshop names the paths a run reaches
([livery.workshop.api.scoped_paths][]) and splits them into calls
([livery.workshop.api.run_batched][]), and a body only calls ruff. Each
check hands ruff the words after ``--`` on its own verb
(``fm lint.ruff -- --statistics``). A body resolves its runner on this
module when it runs, so a test that replaces `run_format` or `run_lint`
here sees its replacement called.
"""

from __future__ import annotations

from pathlib import Path

import livery.toolroom.tools.api as tools
from livery.workshop.api import (
    PATHS,
    CheckRecord,
    Claim,
    Fragment,
    GateContext,
    run_batched,
    scoped_paths,
)

#: The kinds whose python files ruff judges: a python package's, and a
#: native package's ``conanfile.py``, beside clang-format over its
#: sources.
KINDS = ("python", "cpp-conan")

#: The suffixes ruff reads; a foreign file a run names passes through.
SUFFIXES = (".py", ".pyi")

#: What ``--safe-fix`` withholds: rules that delete code an edit in
#: flight has not finished writing, an import added before the code
#: that uses it.
SAFE_UNFIXABLE = "F401"

#: The editor formats python with ruff, as the gate does.
EDITOR = "charliermarsh.ruff"
SETTINGS = """\
{"[python]": {"editor.defaultFormatter": "charliermarsh.ruff"}}
"""


def python_paths(paths: tuple[str, ...]) -> tuple[str, ...]:
    """*paths* without the foreign files: the directories and the python files.

    A directory is ruff's to walk and a named python file its to read;
    a named C++ or markdown file is not, so it passes through as a
    no-op rather than an error.
    """
    return tuple(
        entry
        for entry in paths
        if not Path(entry).is_file() or Path(entry).suffix in SUFFIXES
    )


def run_format(
    *,
    check: bool,
    safe_fix: bool = False,
    paths: tuple[str, ...],
    arguments: tuple[str, ...] = (),
) -> None:
    """Format *paths* with ruff; *check* reports instead of rewriting.

    Formatting removes no code, so *safe_fix* rewrites as a plain fix
    does. A file named in *paths* follows the configured excludes as a
    file ruff finds itself does: ruff applies them to a named file
    only under ``--force-exclude``, and a run handed files names each
    one. *arguments* go to ``ruff format`` before the paths.
    """
    chosen = python_paths(paths)
    if chosen:
        tools.ruff_format(
            *arguments, *chosen, check=check and not safe_fix, force_exclude=True
        )


def run_lint(
    *,
    fix: bool,
    safe_fix: bool = False,
    paths: tuple[str, ...],
    arguments: tuple[str, ...] = (),
) -> None:
    """Lint *paths* with ruff; *fix* applies its fixes, *safe_fix* all but some.

    *safe_fix* withholds `SAFE_UNFIXABLE`, the rules that delete code.
    A named file follows the configured excludes, as `run_format` says.
    *arguments* go to ``ruff check`` before the paths.
    """
    chosen = python_paths(paths)
    if chosen:
        tools.ruff.check(
            *arguments,
            *chosen,
            fix=fix or safe_fix,
            unfixable=SAFE_UNFIXABLE if safe_fix else None,
            force_exclude=True,
        )


def _format_run(ctx: GateContext) -> None:
    run_batched(
        scoped_paths(ctx, "format.ruff"),
        lambda batch: run_format(check=True, paths=batch, arguments=ctx.arguments),
    )


def _format_fix(ctx: GateContext) -> None:
    run_batched(
        scoped_paths(ctx, "format.ruff"),
        lambda batch: run_format(
            check=False, safe_fix=ctx.safe, paths=batch, arguments=ctx.arguments
        ),
    )


def _lint_run(ctx: GateContext) -> None:
    run_batched(
        scoped_paths(ctx, "lint.ruff"),
        lambda batch: run_lint(fix=False, paths=batch, arguments=ctx.arguments),
    )


def _lint_fix(ctx: GateContext) -> None:
    run_batched(
        scoped_paths(ctx, "lint.ruff"),
        lambda batch: run_lint(
            fix=not ctx.safe, safe_fix=ctx.safe, paths=batch, arguments=ctx.arguments
        ),
    )


#: An example keeps the layout its page shows: ruff judges its names,
#: and nothing of its style.
_EXAMPLE_IGNORES = (
    "D",
    "E",
    "I",
    "UP",
    "B",
    "SIM",
    "C4",
    "RUF",
    "F401",
    "F811",
    "F841",
)

CHECKS = (
    CheckRecord(
        "ruff",
        "format",
        _format_run,
        narrowing=PATHS,
        fix=_format_fix,
        kinds=KINDS,
        tools=("ruff",),
        arguments=True,
        fragments=(Fragment(".vscode/settings.json", SETTINGS),),
        editor_extension=EDITOR,
        claims=tuple(
            Claim(category, suffixes=SUFFIXES)
            for category in ("source", "test", "test-support", "configuration")
        ),
    ),
    CheckRecord(
        "ruff",
        "lint",
        _lint_run,
        narrowing=PATHS,
        fix=_lint_fix,
        kinds=KINDS,
        tools=("ruff",),
        arguments=True,
        editor_extension=EDITOR,
        # Test bodies explain themselves by name and assertion, so the
        # docstring rules stop at the tests.
        claims=(
            Claim("source", suffixes=SUFFIXES),
            Claim("test", ignore=("D1",), suffixes=SUFFIXES),
            Claim("example", ignore=_EXAMPLE_IGNORES, suffixes=SUFFIXES),
            Claim("test-support", ignore=("D1",), suffixes=SUFFIXES),
            Claim("configuration", suffixes=SUFFIXES),
        ),
    ),
)
