"""basedpyright's two checks: the type checker, and type completeness as its option.

The extension's ``extension.toml`` declares both and names the bodies
here. ``typecheck.basedpyright`` narrows by paths: the workshop names the
paths a run reaches ([livery.workshop.scoped_paths][]) and splits
them into calls ([livery.workshop.run_batched][]). A run that
reaches the whole calls basedpyright with no path, since basedpyright
reads ``.`` as every file under the root and ignores the configured
``include`` then. ``typecomplete.basedpyright`` narrows by packages
([livery.workshop.scoped_packages][]) and verifies the modules each
one declares public ([livery.workshop.public_modules][]); it
registers only when the workspace lists ``basedpyright[typecomplete]``.
Each check hands basedpyright the words after ``--`` on its own verb
(``fm typecheck.basedpyright -- --level error``). A body resolves its
runner on this module when it runs, so a test that replaces
`run_typecheck` or `run_typecomplete` here sees its replacement called.
"""

from __future__ import annotations

from functools import partial

import livery.toolroom.tools as tools
from livery.workshop import (
    WHOLE,
    GateContext,
    public_modules,
    run_batched,
    scoped_packages,
    scoped_paths,
)


def run_typecheck(paths: tuple[str, ...] = (), arguments: tuple[str, ...] = ()) -> None:
    """Type-check *paths* with basedpyright, its warnings gating as errors.

    No path checks the configured whole: what ``pyrightconfig.json``
    includes, less what it excludes. *arguments* go to basedpyright
    before the paths.
    """
    tools.basedpyright(*arguments, *paths, warnings=True)


def run_typecomplete(modules: tuple[str, ...], arguments: tuple[str, ...] = ()) -> None:
    """Verify that each of *modules* is 100% type-complete.

    Every public symbol needs a fully known type; the exit code is the
    verdict. The verifier reads the ``py.typed`` at the distribution's
    root and follows what a module declares public, so a public package
    a root's ``__init__`` declares is verified through it.
    *arguments* go to basedpyright in each module's call.
    """
    for module in modules:
        tools.basedpyright(*arguments, verifytypes=module, ignoreexternal=True)


def judge_typecheck(ctx: GateContext) -> None:
    """Type-check the paths the run reaches, or the configured whole."""
    chosen = scoped_paths(ctx, "typecheck.basedpyright")
    if chosen == WHOLE:
        run_typecheck(arguments=ctx.arguments)
        return
    run_batched(chosen, partial(run_typecheck, arguments=ctx.arguments))


def judge_typecomplete(ctx: GateContext) -> None:
    """Verify the public modules of each package the run reaches."""
    for package in scoped_packages(ctx, "typecomplete.basedpyright"):
        run_typecomplete(public_modules(package), ctx.arguments)
