"""Ruff's two checks: the formatter, then the linter, over the python files they claim.

The extension's ``extension.toml`` declares both and names the bodies
here. Each check narrows by paths: the workshop names the paths a run reaches
([livery.workshop.scoped_paths][]) and splits them into calls
([livery.workshop.run_batched][]), and a body only calls ruff. Each
check hands ruff the words after ``--`` on its own verb
(``fm lint.ruff -- --statistics``). A body resolves its runner on this
module when it runs, so a test that replaces `run_format` or `run_lint`
here sees its replacement called.
"""

from __future__ import annotations

from pathlib import Path

import livery.toolroom.tools as tools
from livery.workshop import GateContext, run_batched, scoped_paths

#: The suffixes ruff reads; a foreign file a run names passes through.
SUFFIXES = (".py", ".pyi")

#: What ``--safe-fix`` withholds: rules that delete code an edit in
#: flight has not finished writing, an import added before the code
#: that uses it.
SAFE_UNFIXABLE = "F401"


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


def judge_format(ctx: GateContext) -> None:
    """Refuse a file ruff would reformat, over the paths the run reaches."""
    run_batched(
        scoped_paths(ctx, "format.ruff"),
        lambda batch: run_format(check=True, paths=batch, arguments=ctx.arguments),
    )


def fix_format(ctx: GateContext) -> None:
    """Reformat the paths the run reaches."""
    run_batched(
        scoped_paths(ctx, "format.ruff"),
        lambda batch: run_format(
            check=False, safe_fix=ctx.safe, paths=batch, arguments=ctx.arguments
        ),
    )


def judge_lint(ctx: GateContext) -> None:
    """Refuse what ruff's rules find, over the paths the run reaches."""
    run_batched(
        scoped_paths(ctx, "lint.ruff"),
        lambda batch: run_lint(fix=False, paths=batch, arguments=ctx.arguments),
    )


def fix_lint(ctx: GateContext) -> None:
    """Apply ruff's fixes over the paths the run reaches; under --safe-fix, some."""
    run_batched(
        scoped_paths(ctx, "lint.ruff"),
        lambda batch: run_lint(
            fix=not ctx.safe, safe_fix=ctx.safe, paths=batch, arguments=ctx.arguments
        ),
    )
