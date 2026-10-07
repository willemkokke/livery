"""pyrefly's check: the type checker over every platform at once.

``typecheck.pyrefly`` checks what ``pyrefly.toml`` includes whatever a
run reaches: a run costs seconds, and the file pins the platforms,
every one at once. The check hands pyrefly the words after ``--`` on
its own verb (``fm typecheck.pyrefly -- --summarize-errors``). A body
resolves its runner on this module when it runs, so a test that
replaces `run_typecheck` here sees its replacement called.
"""

from __future__ import annotations

import livery.toolroom.tools as tools
from livery.workshop import CheckRecord, Claim, GateContext

#: The kinds whose python files pyrefly judges.
KINDS = ("python",)

#: The suffixes pyrefly reads.
SUFFIXES = (".py", ".pyi")


def run_typecheck(arguments: tuple[str, ...] = ()) -> None:
    """Type-check the configured whole with pyrefly; its exit code is the verdict.

    *arguments* go to ``pyrefly check``.
    """
    tools.pyrefly("check", *arguments)


def _typecheck_run(ctx: GateContext) -> None:
    run_typecheck(ctx.arguments)


CHECKS = (
    CheckRecord(
        "pyrefly",
        "typecheck",
        _typecheck_run,
        kinds=KINDS,
        tools=("pyrefly",),
        arguments=True,
        claims=tuple(
            Claim(category, suffixes=SUFFIXES)
            for category in ("source", "test", "test-support")
        ),
    ),
)
