"""ty's check: the type checker over every platform at once.

The extension's ``extension.toml`` declares it and names the body here.
``typecheck.ty`` checks what ``ty.toml`` includes whatever a run
reaches: a run costs seconds, and the file pins the platforms, every
one at once. The check hands ty the words after ``--`` on its own
verb (``fm typecheck.ty -- --output-format concise``). A body resolves
its runner on this module when it runs, so a test that replaces
`run_typecheck` here sees its replacement called.
"""

from __future__ import annotations

import livery.toolroom.tools as tools
from livery.workshop import GateContext


def run_typecheck(arguments: tuple[str, ...] = ()) -> None:
    """Type-check the configured whole with ty; its exit code is the verdict.

    *arguments* go to ``ty check``.
    """
    tools.ty.check(*arguments)


def judge_typecheck(ctx: GateContext) -> None:
    """Type-check the configured whole; the words after ``--`` reach ty."""
    run_typecheck(ctx.arguments)
