"""ty's check: the type checker over every platform at once.

``typecheck.ty`` checks what ``ty.toml`` includes whatever a run
reaches: a run costs seconds, and the file pins the platforms, every
one at once. A body resolves its runner on this module when it runs,
so a test that replaces `run_typecheck` here sees its replacement
called.
"""

from __future__ import annotations

import livery.toolroom.tools.api as tools
from livery.workshop.api import CheckRecord, Claim, GateContext

#: The kinds whose python files ty judges.
KINDS = ("python",)

#: The suffixes ty reads.
SUFFIXES = (".py", ".pyi")

#: The editor extension that answers with ty's verdict as the gate's.
EDITOR = "astral-sh.ty"


def run_typecheck() -> None:
    """Type-check the configured whole with ty; its exit code is the verdict."""
    tools.ty.check()


def _typecheck_run(ctx: GateContext) -> None:
    del ctx
    run_typecheck()


CHECKS = (
    CheckRecord(
        "ty",
        "typecheck",
        _typecheck_run,
        kinds=KINDS,
        tools=("ty",),
        editor_extension=EDITOR,
        claims=tuple(
            Claim(category, suffixes=SUFFIXES)
            for category in ("source", "test", "test-support")
        ),
    ),
)
