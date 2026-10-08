"""basedpyright's type completeness, the check its words cannot say.

The type checker, ``typecheck.basedpyright``, is basedpyright's words in
the extension's ``extension.toml``. ``typecomplete.basedpyright`` runs
one call per public module and reads the verifier's verdict for each,
so it is code: it narrows by packages
([livery.workshop.scoped_packages][]) and verifies the modules each
one declares public ([livery.workshop.public_modules][]); it
registers only when the workspace lists ``basedpyright[typecomplete]``.
It hands basedpyright the words after ``--`` on its own verb
(``fm typecomplete.basedpyright -- --outputjson``). The body resolves
its runner on this module when it runs, so a test that replaces
`run_typecomplete` here sees its replacement called.
"""

from __future__ import annotations

import livery.toolroom.tools as tools
from livery.workshop import GateContext, public_modules, scoped_packages


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


def judge_typecomplete(ctx: GateContext) -> None:
    """Verify the public modules of each package the run reaches."""
    for package in scoped_packages(ctx, "typecomplete.basedpyright"):
        run_typecomplete(public_modules(package), ctx.arguments)
