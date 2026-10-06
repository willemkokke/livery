"""basedpyright's two checks: the type checker, and type completeness as its option.

``typecheck.basedpyright`` narrows by paths: the workshop names the
paths a run reaches ([livery.workshop.api.scoped_paths][]) and splits
them into calls ([livery.workshop.api.run_batched][]). A run that
reaches the whole calls basedpyright with no path, since basedpyright
reads ``.`` as every file under the root and ignores the configured
``include`` then. ``typecomplete.basedpyright`` narrows by packages
([livery.workshop.api.scoped_packages][]) and verifies the modules each
one declares public ([livery.workshop.api.public_modules][]); it
registers only when the workspace lists ``basedpyright[typecomplete]``.
Each check hands basedpyright the words after ``--`` on its own verb
(``fm typecheck.basedpyright -- --level error``). A body resolves its
runner on this module when it runs, so a test that replaces
`run_typecheck` or `run_typecomplete` here sees its replacement called.
"""

from __future__ import annotations

from functools import partial

import livery.toolroom.tools.api as tools
from livery.workshop.api import (
    PACKAGES,
    PATHS,
    WHOLE,
    CheckRecord,
    Claim,
    Fragment,
    GateContext,
    public_modules,
    run_batched,
    scoped_packages,
    scoped_paths,
)

#: The kinds whose python files basedpyright judges.
KINDS = ("python",)

#: The suffixes basedpyright reads.
SUFFIXES = (".py", ".pyi")

#: The option that registers the type-completeness check.
TYPECOMPLETE = "typecomplete"

#: The editor's language server: basedpyright's own, reading the same file.
EDITOR = "detachhead.basedpyright"

#: The type checker that answers in the editor is the one this
#: workspace configures: the editor's default python language server
#: is off, so it adds no second verdict with settings of its own.
SETTINGS = """\
{"python.languageServer": "None"}
"""


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
    a namespace root's ``api`` declares is verified through it.
    *arguments* go to basedpyright in each module's call.
    """
    for module in modules:
        tools.basedpyright(*arguments, verifytypes=module, ignoreexternal=True)


def _typecheck_run(ctx: GateContext) -> None:
    chosen = scoped_paths(ctx, "typecheck.basedpyright")
    if chosen == WHOLE:
        run_typecheck(arguments=ctx.arguments)
        return
    run_batched(chosen, partial(run_typecheck, arguments=ctx.arguments))


def _typecomplete_run(ctx: GateContext) -> None:
    for package in scoped_packages(ctx, "typecomplete.basedpyright"):
        run_typecomplete(public_modules(package), ctx.arguments)


CHECKS = (
    CheckRecord(
        "basedpyright",
        "typecheck",
        _typecheck_run,
        narrowing=PATHS,
        kinds=KINDS,
        tools=("basedpyright",),
        arguments=True,
        fragments=(Fragment(".vscode/settings.json", SETTINGS),),
        editor_extension=EDITOR,
        claims=tuple(
            Claim(category, suffixes=SUFFIXES)
            for category in ("source", "test", "test-support")
        ),
    ),
    CheckRecord(
        "basedpyright",
        "typecomplete",
        _typecomplete_run,
        narrowing=PACKAGES,
        kinds=KINDS,
        tools=("basedpyright",),
        arguments=True,
        claims=(Claim("source", suffixes=SUFFIXES),),
        listed_with=TYPECOMPLETE,
    ),
)
