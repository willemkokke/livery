"""The cmake extension's steps: the gate build's configure and compile, and ctest.

Each runs over one package whose set holds cmake, on the tree side of
its phase, and hands cmake or ctest the words after ``--`` on its own
verb. A body looks its function up on the gate build's module when it
runs, so a test that replaces one there sees its replacement called.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from livery.extensions.cmake import _build

if TYPE_CHECKING:
    from livery.workshop import PhaseContext


def configure(ctx: PhaseContext) -> None:
    """Configure the package's gate build into ``build/gate``."""
    _build.configure(ctx.member, ctx.arguments)


def compile(ctx: PhaseContext) -> None:
    """Build the configured gate build, incrementally."""
    _build.compile(ctx.member, ctx.arguments)


def ctest(ctx: PhaseContext) -> None:
    """Run ctest over the gate build: every test, or the selection's alone."""
    _build.test(ctx.member, ctx.root, selection=ctx.selection, arguments=ctx.arguments)
