"""mypy's check: the type checker, once per platform, over the python files it claims.

``typecheck.mypy`` narrows by paths: the workshop names the paths a run
reaches ([livery.workshop.api.scoped_paths][]) and splits them into
calls ([livery.workshop.api.run_batched][]); a run that reaches the
whole calls mypy with no path, so it reads the ``files`` of
``mypy.ini``. mypy has no all-platforms mode, so every call checks
linux, darwin and win32 in parallel, each with a cache of its own:
mypy's SQLite cache takes one writer. A body resolves its runner on
this module when it runs, so a test that replaces `run_typecheck` here
sees its replacement called.
"""

from __future__ import annotations

import livery.toolroom.tools.api as tools
from livery.footman.api import parallel, step
from livery.workshop.api import (
    PATHS,
    WHOLE,
    CheckRecord,
    Claim,
    GateContext,
    run_batched,
    scoped_paths,
)

#: The kinds whose python files mypy judges.
KINDS = ("python",)

#: The suffixes mypy reads.
SUFFIXES = (".py", ".pyi")

#: The platforms every call checks, linux first: a bare ``mypy`` checks
#: linux, the platform ``mypy.ini`` names.
PLATFORMS = ("linux", "darwin", "win32")

#: Where the caches live, a directory per platform, with the workshop's
#: state and never in the root.
CACHE = ".workshop/.cache/mypy"


def run_typecheck(paths: tuple[str, ...] = ()) -> None:
    """Type-check *paths* with mypy on every platform, in parallel.

    No path checks the configured ``files``. Each platform's verdict is
    its own, and every one gates.
    """

    def on(platform: str) -> None:
        tools.mypy(*paths, platform=platform, cache_dir=f"{CACHE}/{platform}")

    parallel(*(step(on, title=f"mypy_{platform}")(platform) for platform in PLATFORMS))


def _typecheck_run(ctx: GateContext) -> None:
    chosen = scoped_paths(ctx, "typecheck.mypy")
    if chosen == WHOLE:
        run_typecheck()
        return
    run_batched(chosen, run_typecheck)


CHECKS = (
    CheckRecord(
        "mypy",
        "typecheck",
        _typecheck_run,
        narrowing=PATHS,
        kinds=KINDS,
        tools=("mypy",),
        claims=tuple(
            Claim(category, suffixes=SUFFIXES)
            for category in ("source", "test", "test-support")
        ),
        # mypy reads the members' own stubs from the venv, so it rides
        # the dev group beside the store's copy.
        contributions=(("python.dev-group", "mypy>=1.14"),),
    ),
)
